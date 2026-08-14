"""Attachments: upload, download, remove.

Phase 2 is storage only. Nothing here opens a file, extracts text from it, or
sends it to a model — that is Phase 6, with the review that document parsing
deserves. What Phase 2 does owe is safety: validate before storing, store under a
key the client cannot influence, and serve back in a way that a browser will never
execute.

``scan_status`` stays ``pending`` because malware scanning is not implemented. It
is recorded rather than assumed, so no later phase can mistake an unscanned file
for a clean one.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from janus_core.errors import AuthorizationError, NotFoundError, ValidationError
from janus_core.ids import IdPrefix, new_id
from janus_core.logging import get_logger
from janus_schemas.common import Classification
from sqlalchemy import func, select

from api_app.deps import (
    ConversationsDep,
    ObjectStoreDep,
    Principal,
    PrincipalDep,
    SessionDep,
    SettingsDep,
)
from api_app.models import Attachment, Organization
from api_app.schemas import AttachmentResponse
from api_app.storage import safe_filename, storage_key, validate_upload

logger = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["attachments"])

#: Serving uploads is the classic stored-XSS route, so every download is forced to
#: be a download: never rendered, never sniffed, never allowed to run scripts.
DOWNLOAD_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, no-store",
}


def _require_user(principal: Principal) -> str:
    if principal.kind != "user" or principal.user_id is None:
        raise AuthorizationError(
            "Attachments are only available to a signed-in user.", code="session_required"
        )
    return principal.user_id


def _response(attachment: Attachment) -> AttachmentResponse:
    return AttachmentResponse(
        id=attachment.id,
        filename=attachment.filename,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        scan_status=attachment.scan_status,
        created_at=attachment.created_at,
    )


@router.post(
    "/conversations/{conversation_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    conversation_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    conversations: ConversationsDep,
    store: ObjectStoreDep,
    settings: SettingsDep,
    file: UploadFile = File(...),
    mime_type: str | None = Form(default=None),
) -> AttachmentResponse:
    user_id = _require_user(principal)
    conversation = await conversations.get(session, conversation_id, user_id=user_id)

    pending = await session.scalar(
        select(func.count())
        .select_from(Attachment)
        .where(
            Attachment.conversation_id == conversation_id,
            Attachment.message_id.is_(None),
        )
    )
    if (pending or 0) >= settings.attachment_max_per_message:
        raise ValidationError(
            "Too many files are waiting to be sent in this conversation.",
            param="file",
            details={"limit": settings.attachment_max_per_message},
        )

    # Read with the limit as the ceiling: reading an arbitrarily large body into
    # memory first and validating afterwards would be the vulnerability.
    data = await file.read(settings.attachment_max_bytes + 1)
    declared = mime_type or file.content_type or ""
    filename = safe_filename(file.filename or "upload")
    checksum = validate_upload(
        filename=filename,
        mime_type=declared,
        data=data,
        max_bytes=settings.attachment_max_bytes,
    )

    organization = await session.get(Organization, principal.organization_id)
    attachment_id = new_id(IdPrefix.ATTACHMENT)
    key = storage_key(
        organization_id=principal.organization_id,
        conversation_id=conversation_id,
        attachment_id=attachment_id,
    )
    await store.put(key, data)

    attachment = Attachment(
        id=attachment_id,
        organization_id=principal.organization_id,
        conversation_id=conversation.id,
        uploaded_by=user_id,
        filename=filename,
        mime_type=declared.split(";")[0].strip().lower(),
        size_bytes=len(data),
        storage_key=key,
        checksum_sha256=checksum,
        # The organization's default, so a stricter tenant does not get a laxer
        # label on its files than on its conversations.
        classification=(
            organization.default_classification if organization else Classification.INTERNAL
        ),
    )
    session.add(attachment)
    await session.flush()

    logger.info(
        "attachment_stored",
        extra={
            "attachment_id": attachment.id,
            "conversation_id": conversation_id,
            "size_bytes": attachment.size_bytes,
            "mime_type": attachment.mime_type,
        },
    )
    return _response(attachment)


@router.get("/attachments/{attachment_id}/content", response_model=None)
async def download_attachment(
    attachment_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    store: ObjectStoreDep,
) -> StreamingResponse:
    user_id = _require_user(principal)
    attachment = await session.get(Attachment, attachment_id)
    if attachment is None or attachment.uploaded_by != user_id:
        raise NotFoundError("Attachment not found.", code="attachment_not_found")

    # ASCII fallback plus the RFC 5987 form, so a non-Latin filename survives and
    # a quote in it cannot break out of the header.
    ascii_name = attachment.filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
    disposition = f'attachment; filename="{ascii_name}"'

    return StreamingResponse(
        store.open(attachment.storage_key),
        media_type="application/octet-stream",
        headers={**DOWNLOAD_HEADERS, "Content-Disposition": disposition},
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    store: ObjectStoreDep,
) -> Response:
    """Remove a file that has not been sent yet.

    Once a message carries an attachment it is part of a transcript, and the
    transcript is not editable; deleting the conversation removes both.
    """
    user_id = _require_user(principal)
    attachment = await session.get(Attachment, attachment_id)
    if attachment is None or attachment.uploaded_by != user_id:
        raise NotFoundError("Attachment not found.", code="attachment_not_found")
    if attachment.message_id is not None:
        raise ValidationError(
            "This file has already been sent.",
            param="attachment_id",
            details={"hint": "delete the conversation to remove it"},
        )

    key = attachment.storage_key
    await session.delete(attachment)
    await session.flush()
    await store.delete(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
