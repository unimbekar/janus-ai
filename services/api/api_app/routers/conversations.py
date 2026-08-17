"""Conversations: the web app's primary API.

The browser talks to this router, not to ``/v1/chat`` — a conversation is the
product, and the stateless completion endpoint is for programmatic callers
(ADR 0008). Sending a message therefore does three things in order: persist what
the person wrote, ask the gateway for an answer, and persist the answer with the
attribution the gateway reported.

Only a user session may reach these endpoints. Conversations belong to a person,
and an API key represents an integration, so a key that could read someone's chat
history would be a privilege the product never intends to grant.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import StreamingResponse
from janus_core.errors import AuthorizationError, NotFoundError, ValidationError
from janus_core.logging import get_logger
from janus_schemas.common import Classification, ExecutionMode

from api_app.chat_stream import ChatRunner, Turn
from api_app.conversations import content_text
from api_app.deps import (
    CancellationDep,
    ClassificationDep,
    ConversationsDep,
    DatabaseDep,
    GatewayDep,
    ModeDep,
    Principal,
    PrincipalDep,
    RequestIdDep,
    SessionDep,
)
from api_app.knowledge import KnowledgeService, ground_messages
from api_app.models import Attachment, Conversation, Message
from api_app.schemas import (
    AttachmentResponse,
    CancelResponse,
    ConversationDetailResponse,
    ConversationPageResponse,
    ConversationResponse,
    CreateConversationRequest,
    MessageResponse,
    SendMessageRequest,
    UpdateConversationRequest,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/v1/conversations", tags=["conversations"])

STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    # Proxies that buffer would defeat streaming; nginx and friends honor this.
    "X-Accel-Buffering": "no",
}


def _require_user(principal: Principal) -> str:
    if principal.kind != "user" or principal.user_id is None:
        raise AuthorizationError(
            "Conversations are only available to a signed-in user.",
            code="session_required",
        )
    return principal.user_id


def _conversation_response(conversation: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        pinned_model=conversation.pinned_model,
        mode=conversation.mode,
        message_count=conversation.message_count,
        last_message_at=conversation.last_message_at,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _attachment_response(attachment: Attachment) -> AttachmentResponse:
    return AttachmentResponse(
        id=attachment.id,
        filename=attachment.filename,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        scan_status=attachment.scan_status,
        created_at=attachment.created_at,
    )


def _message_response(
    message: Message,
    attachments: list[Attachment] | None = None,
    citations: list[dict] | None = None,
) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        role=message.role,
        sequence=message.sequence,
        content=content_text(message.content),
        status=message.status,
        model=message.model_slug,
        deployment=message.deployment_key,
        provider=message.provider,
        privacy=message.privacy,
        fallback_used=message.fallback_used,
        routing_explanation=message.routing_explanation,
        input_tokens=message.input_tokens,
        output_tokens=message.output_tokens,
        finish_reason=message.finish_reason,
        error=message.error,
        parent_message_id=message.parent_message_id,
        attachments=[_attachment_response(item) for item in attachments or []],
        citations=citations or [],
        created_at=message.created_at,
    )


def _narrowed_mode(organization_mode: ExecutionMode, conversation: Conversation) -> ExecutionMode:
    """Most restrictive of the organization's mode and the conversation's own.

    A conversation may be stricter than its organization, never looser: the
    ceiling is set by policy, and a per-thread setting is a request, not an
    override.
    """
    if conversation.mode is None:
        return organization_mode
    return max(organization_mode, conversation.mode, key=lambda value: value.restrictiveness)


def _narrowed_classification(default: Classification, conversation: Conversation) -> Classification:
    if conversation.classification is None:
        return default
    return max(default, conversation.classification, key=lambda value: value.rank)


# ------------------------------------------------------------- conversations


@router.get("", response_model=ConversationPageResponse)
async def list_conversations(
    principal: PrincipalDep,
    session: SessionDep,
    conversations: ConversationsDep,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> ConversationPageResponse:
    user_id = _require_user(principal)
    page = await conversations.list_recent(session, user_id=user_id, limit=limit, cursor=cursor)
    return ConversationPageResponse(
        data=[_conversation_response(item) for item in page.conversations],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: CreateConversationRequest,
    principal: PrincipalDep,
    session: SessionDep,
    conversations: ConversationsDep,
) -> ConversationResponse:
    user_id = _require_user(principal)
    conversation = await conversations.create(
        session,
        organization_id=principal.organization_id,
        user_id=user_id,
        title=body.title,
        pinned_model=body.pinned_model,
        mode=body.mode,
    )
    return _conversation_response(conversation)


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    conversations: ConversationsDep,
) -> ConversationDetailResponse:
    user_id = _require_user(principal)
    conversation = await conversations.get(session, conversation_id, user_id=user_id)
    messages = await conversations.messages(session, conversation_id)
    attachments = await conversations.attachments_for(session, [item.id for item in messages])
    citations = await KnowledgeService().citations_for(session, [item.id for item in messages])

    return ConversationDetailResponse(
        **_conversation_response(conversation).model_dump(),
        messages=[
            _message_response(
                message, attachments.get(message.id), citations.get(message.id)
            )
            for message in messages
        ],
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    principal: PrincipalDep,
    session: SessionDep,
    conversations: ConversationsDep,
) -> ConversationResponse:
    user_id = _require_user(principal)
    conversation = await conversations.get(session, conversation_id, user_id=user_id)
    await conversations.update(
        session,
        conversation,
        title=body.title,
        pinned_model=body.pinned_model,
        clear_pinned_model=body.clear_pinned_model,
        mode=body.mode,
    )
    return _conversation_response(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    conversations: ConversationsDep,
) -> Response:
    user_id = _require_user(principal)
    conversation = await conversations.get(session, conversation_id, user_id=user_id)
    await conversations.soft_delete(session, conversation)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ messages


@router.post("/{conversation_id}/messages", response_model=None)
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    principal: PrincipalDep,
    db: DatabaseDep,
    conversations: ConversationsDep,
    gateway: GatewayDep,
    cancellations: CancellationDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> StreamingResponse:
    """Persist the user's message, then stream the assistant's reply.

    The user's turn is committed in its own transaction before the model is
    called, so a model failure never costs someone the text they wrote.
    """
    user_id = _require_user(principal)

    async with db.session(organization_id=principal.organization_id, user_id=user_id) as session:
        conversation = await conversations.get(session, conversation_id, user_id=user_id)
        user_message = await conversations.append_user_message(
            session,
            conversation,
            text=body.content,
            attachment_ids=body.attachment_ids,
        )
        assistant_message = await conversations.start_assistant_message(session, conversation)
        history = await conversations.messages(session, conversation_id)

        prompt = conversations.prompt_messages(history)
        rag = KnowledgeService()
        hits = await rag.retrieve_for_organization(
            session,
            query=body.content,
            gateway=gateway,
            organization_id=principal.organization_id,
            request_id=request_id,
            mode=mode,
            classification=classification,
        )
        citations = []
        if hits:
            prompt = ground_messages(prompt, hits)
            citations = await rag.persist_citations(
                session,
                organization_id=principal.organization_id,
                message_id=assistant_message.id,
                hits=hits,
            )
        turn = Turn(
            conversation_id=conversation.id,
            organization_id=conversation.organization_id,
            user_id=user_id,
            actor_id=principal.actor_id,
            assistant_message_id=assistant_message.id,
            sequence=assistant_message.sequence,
            user_message_id=user_message.id,
        )
        model = body.model or conversation.pinned_model or "auto"
        effective_mode = _narrowed_mode(mode, conversation)
        effective_classification = _narrowed_classification(classification, conversation)

    runner = ChatRunner(db, conversations, gateway, cancellations)
    return StreamingResponse(
        runner.run(
            turn,
            prompt=prompt,
            model=model,
            mode=effective_mode,
            classification=effective_classification,
            request_id=request_id,
            citations=citations,
        ),
        media_type="text/event-stream",
        headers={**STREAM_HEADERS, "X-Janus-Request-Id": request_id},
    )


@router.post("/{conversation_id}/messages/{message_id}/regenerate", response_model=None)
async def regenerate_message(
    conversation_id: str,
    message_id: str,
    principal: PrincipalDep,
    db: DatabaseDep,
    conversations: ConversationsDep,
    gateway: GatewayDep,
    cancellations: CancellationDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
    model: str | None = Query(default=None, description="Try a different model."),
) -> StreamingResponse:
    """Answer again, keeping the previous attempt.

    The old message is not edited — the database would refuse, and a transcript
    that silently changes is not a transcript. The new turn is appended with
    ``parent_message_id`` pointing at what it replaces, and the prompt is the
    history *before* that attempt.
    """
    user_id = _require_user(principal)

    async with db.session(organization_id=principal.organization_id, user_id=user_id) as session:
        conversation = await conversations.get(session, conversation_id, user_id=user_id)
        history = await conversations.messages(session, conversation_id)

        target = next((item for item in history if item.id == message_id), None)
        if target is None:
            raise NotFoundError("Message not found.", code="message_not_found")
        if target.role != "assistant":
            raise ValidationError(
                "Only an assistant message can be regenerated.",
                param="message_id",
            )
        if target.status == "streaming":
            raise ValidationError(
                "That answer is still being written.",
                param="message_id",
                details={"hint": "cancel it first, or wait for it to finish"},
            )

        prompt = conversations.prompt_messages(
            [item for item in history if item.sequence < target.sequence]
        )
        if not prompt:
            raise ValidationError(
                "There is nothing to answer before that message.", param="message_id"
            )

        assistant_message = await conversations.start_assistant_message(
            session, conversation, parent_message_id=target.id
        )
        last_user = next(
            (item["content"] for item in reversed(prompt) if item["role"] == "user"),
            "",
        )
        rag = KnowledgeService()
        hits = await rag.retrieve_for_organization(
            session,
            query=last_user,
            gateway=gateway,
            organization_id=principal.organization_id,
            request_id=request_id,
            mode=mode,
            classification=classification,
        )
        citations: list[dict] = []
        if hits:
            prompt = ground_messages(prompt, hits)
            citations = await rag.persist_citations(
                session,
                organization_id=principal.organization_id,
                message_id=assistant_message.id,
                hits=hits,
            )
        turn = Turn(
            conversation_id=conversation.id,
            organization_id=conversation.organization_id,
            user_id=user_id,
            actor_id=principal.actor_id,
            assistant_message_id=assistant_message.id,
            sequence=assistant_message.sequence,
        )
        effective_mode = _narrowed_mode(mode, conversation)
        effective_classification = _narrowed_classification(classification, conversation)
        chosen_model = model or conversation.pinned_model or "auto"

    runner = ChatRunner(db, conversations, gateway, cancellations)
    return StreamingResponse(
        runner.run(
            turn,
            prompt=prompt,
            model=chosen_model,
            mode=effective_mode,
            classification=effective_classification,
            request_id=request_id,
            citations=citations,
        ),
        media_type="text/event-stream",
        headers={**STREAM_HEADERS, "X-Janus-Request-Id": request_id},
    )


@router.post("/{conversation_id}/cancel", response_model=CancelResponse)
async def cancel_generation(
    conversation_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    conversations: ConversationsDep,
    cancellations: CancellationDep,
) -> CancelResponse:
    """Stop whatever is being generated in this conversation.

    A client that is holding the stream open cancels by closing it. This exists
    for the other cases — a second tab, another device — and only reaches
    generations running in this process until Phase 3 makes cancellation
    cross-instance.
    """
    user_id = _require_user(principal)
    await conversations.get(session, conversation_id, user_id=user_id)
    return CancelResponse(cancelled=cancellations.cancel(conversation_id))
