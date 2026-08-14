"""Conversations and messages: the persistence behind the chat product.

Three rules shape this module.

**The user's message is committed before the model is called.** If the model call
fails, what the person typed is still there. That ordering is why sending is two
transactions rather than one.

**Sequence numbers come from the conversation row, not from ``max(sequence) + 1``.**
Incrementing ``message_count`` takes a row lock, so two tabs sending at the same
moment serialize and receive distinct sequences instead of racing to insert the
same one.

**Finalized messages are never rewritten.** The database enforces it with a
trigger; this module only ever finalizes a row that is still ``streaming``.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from janus_core.errors import NotFoundError, ValidationError
from janus_core.ids import IdPrefix, new_id
from janus_core.logging import get_logger
from janus_schemas.common import Classification, ExecutionMode
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_app.models import Attachment, Conversation, Message

logger = get_logger(__name__)

#: Statuses whose text belongs in the prompt for the next turn. A failed or
#: cancelled turn is shown in the transcript but not replayed to a model, because
#: half an answer is worse context than no answer.
REPLAYABLE_STATUSES = ("complete",)

MAX_TITLE_LENGTH = 80
DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100


def text_content(text: str) -> list[dict[str, Any]]:
    """Wrap plain text as content parts."""
    return [{"type": "text", "text": text}]


def content_text(content: list[dict[str, Any]] | None) -> str:
    """Concatenate the text parts of a message, ignoring parts we cannot render."""
    if not content:
        return ""
    return "".join(
        str(part.get("text", ""))
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def derive_title(text: str) -> str:
    """A first-message title, so the sidebar is readable without asking a model.

    Generating titles with a model is a Phase 3 nicety; it costs a request and
    would be a strange thing to spend the user's first token budget on.
    """
    condensed = " ".join(text.split())
    if len(condensed) <= MAX_TITLE_LENGTH:
        return condensed or "New conversation"
    return condensed[: MAX_TITLE_LENGTH - 1].rstrip() + "…"


def encode_cursor(sort_key: datetime, identifier: str) -> str:
    return base64.urlsafe_b64encode(f"{sort_key.isoformat()}|{identifier}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        timestamp, _, identifier = raw.partition("|")
        return datetime.fromisoformat(timestamp), identifier
    except (ValueError, binascii.Error) as exc:
        raise ValidationError("The pagination cursor is not valid.", param="cursor") from exc


@dataclass(frozen=True, slots=True)
class Page:
    """One page of conversations, with the cursor to continue from."""

    conversations: list[Conversation]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class Attribution:
    """What answered, as reported by the gateway's routing event."""

    model_slug: str | None = None
    deployment_key: str | None = None
    provider: str | None = None
    privacy: str | None = None
    fallback_used: bool = False
    routing_explanation: str | None = None
    request_id: str | None = None


class ConversationService:
    """Data access for threads and turns. Every method runs tenant-scoped."""

    # ---------------------------------------------------------- conversations

    async def create(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        user_id: str,
        title: str | None = None,
        pinned_model: str | None = None,
        mode: ExecutionMode | None = None,
        classification: Classification | None = None,
    ) -> Conversation:
        conversation = Conversation(
            id=new_id(IdPrefix.CONVERSATION),
            organization_id=organization_id,
            user_id=user_id,
            title=title.strip() if title else None,
            pinned_model=pinned_model,
            mode=mode,
            classification=classification,
        )
        session.add(conversation)
        await session.flush()
        return conversation

    async def list_recent(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page:
        """Most recently active first, keyset paginated.

        Ordering on activity rather than creation is what makes the list useful,
        and the keyset is ``(activity, id)`` so a page boundary cannot repeat or
        skip a row when two conversations share a timestamp.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        activity = func.coalesce(Conversation.last_message_at, Conversation.created_at)

        query = (
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
            .order_by(activity.desc(), Conversation.id.desc())
            .limit(limit + 1)
        )
        if cursor:
            cursor_activity, cursor_id = decode_cursor(cursor)
            query = query.where(
                (activity < cursor_activity)
                | ((activity == cursor_activity) & (Conversation.id < cursor_id))
            )

        rows = list((await session.scalars(query)).all())
        has_more = len(rows) > limit
        page = rows[:limit]

        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = encode_cursor(last.last_message_at or last.created_at, last.id)
        return Page(conversations=page, next_cursor=next_cursor)

    async def get(
        self, session: AsyncSession, conversation_id: str, *, user_id: str
    ) -> Conversation:
        """Load a conversation the caller owns.

        Row-level security already restricts this to the organization; the
        ``user_id`` check is the second half, because a colleague's private thread
        is not shared just by being in the same tenant.
        """
        conversation = await session.get(Conversation, conversation_id)
        if (
            conversation is None
            or conversation.deleted_at is not None
            or conversation.user_id != user_id
        ):
            raise NotFoundError("Conversation not found.", code="conversation_not_found")
        return conversation

    async def update(
        self,
        session: AsyncSession,
        conversation: Conversation,
        *,
        title: str | None = None,
        pinned_model: str | None = None,
        clear_pinned_model: bool = False,
        mode: ExecutionMode | None = None,
    ) -> Conversation:
        if title is not None:
            conversation.title = title.strip()[:MAX_TITLE_LENGTH] or None
        if clear_pinned_model:
            conversation.pinned_model = None
        elif pinned_model is not None:
            conversation.pinned_model = pinned_model
        if mode is not None:
            conversation.mode = mode
        await session.flush()
        # ``updated_at`` is set by the database, and an async session may not fetch
        # it implicitly on attribute access. Refreshing here keeps the response
        # honest instead of raising at serialization time.
        await session.refresh(conversation)
        return conversation

    async def soft_delete(self, session: AsyncSession, conversation: Conversation) -> None:
        conversation.deleted_at = datetime.now(UTC)
        await session.flush()

    # -------------------------------------------------------------- messages

    async def messages(
        self, session: AsyncSession, conversation_id: str, *, limit: int | None = None
    ) -> list[Message]:
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence)
        )
        if limit is not None:
            query = query.limit(limit)
        return list((await session.scalars(query)).all())

    async def next_sequence(self, session: AsyncSession, conversation_id: str) -> int:
        """Reserve the next slot in the thread.

        A single ``UPDATE … RETURNING`` is both the allocation and the lock:
        concurrent senders queue on this row rather than colliding on the
        ``(conversation_id, sequence)`` unique constraint.
        """
        sequence = await session.scalar(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
            .values(message_count=Conversation.message_count + 1, last_message_at=func.now())
            .returning(Conversation.message_count)
            .execution_options(synchronize_session=False)
        )
        if sequence is None:
            raise NotFoundError("Conversation not found.", code="conversation_not_found")
        return int(sequence)

    async def append_user_message(
        self,
        session: AsyncSession,
        conversation: Conversation,
        *,
        text: str,
        attachment_ids: list[str] | None = None,
    ) -> Message:
        message = Message(
            id=new_id(IdPrefix.MESSAGE),
            conversation_id=conversation.id,
            organization_id=conversation.organization_id,
            role="user",
            sequence=await self.next_sequence(session, conversation.id),
            content=text_content(text),
            status="complete",
            completed_at=datetime.now(UTC),
        )
        session.add(message)

        if not conversation.title:
            conversation.title = derive_title(text)

        if attachment_ids:
            await self._attach(
                session,
                conversation=conversation,
                message_id=message.id,
                attachment_ids=attachment_ids,
            )

        await session.flush()
        return message

    async def start_assistant_message(
        self,
        session: AsyncSession,
        conversation: Conversation,
        *,
        parent_message_id: str | None = None,
    ) -> Message:
        """Insert the row the stream will fill in.

        It exists before the first token so that a cancelled or crashed stream
        still leaves a visible turn in the transcript rather than a gap.
        """
        message = Message(
            id=new_id(IdPrefix.MESSAGE),
            conversation_id=conversation.id,
            organization_id=conversation.organization_id,
            role="assistant",
            sequence=await self.next_sequence(session, conversation.id),
            content=[],
            status="streaming",
            parent_message_id=parent_message_id,
        )
        session.add(message)
        await session.flush()
        return message

    async def finalize_assistant_message(
        self,
        session: AsyncSession,
        message_id: str,
        *,
        text: str,
        status: str,
        attribution: Attribution | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        finish_reason: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Write the completed turn.

        Scoped to rows still ``streaming``: the finalization can be reached twice
        (a client disconnecting as the stream ends, for instance) and the second
        attempt must be a no-op rather than an error or an overwrite.
        """
        attribution = attribution or Attribution()
        await session.execute(
            update(Message)
            .where(Message.id == message_id, Message.status == "streaming")
            .values(
                content=text_content(text) if text else [],
                status=status,
                model_slug=attribution.model_slug,
                deployment_key=attribution.deployment_key,
                provider=attribution.provider,
                privacy=attribution.privacy,
                fallback_used=attribution.fallback_used,
                routing_explanation=attribution.routing_explanation,
                request_id=attribution.request_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason,
                error=error,
                completed_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )

    def prompt_messages(self, messages: list[Message]) -> list[dict[str, str]]:
        """The transcript as the gateway wants it.

        Only completed turns with text are replayed. An errored or cancelled
        assistant turn stays in the user's view of history but is not presented to
        a model as if it were an answer.
        """
        prompt: list[dict[str, str]] = []
        for message in messages:
            if message.status not in REPLAYABLE_STATUSES:
                continue
            text = content_text(message.content)
            if not text:
                continue
            prompt.append({"role": message.role, "content": text})
        return prompt

    # ------------------------------------------------------------ attachments

    async def _attach(
        self,
        session: AsyncSession,
        *,
        conversation: Conversation,
        message_id: str,
        attachment_ids: list[str],
    ) -> None:
        """Bind uploaded files to the message that carries them.

        Restricted to this conversation's own unattached uploads, so an id
        belonging to another thread — or already used — cannot be re-pointed.
        """
        result = await session.execute(
            update(Attachment)
            .where(
                Attachment.id.in_(attachment_ids),
                Attachment.conversation_id == conversation.id,
                Attachment.message_id.is_(None),
            )
            .values(message_id=message_id)
            .returning(Attachment.id)
            .execution_options(synchronize_session=False)
        )
        bound = set(result.scalars().all())
        missing = [value for value in attachment_ids if value not in bound]
        if missing:
            raise ValidationError(
                "One or more attachments are not available for this conversation.",
                param="attachment_ids",
                details={"unavailable": missing},
            )

    async def attachments_for(
        self, session: AsyncSession, message_ids: list[str]
    ) -> dict[str, list[Attachment]]:
        if not message_ids:
            return {}
        rows = await session.scalars(
            select(Attachment)
            .where(Attachment.message_id.in_(message_ids))
            .order_by(Attachment.created_at)
        )
        grouped: dict[str, list[Attachment]] = {}
        for attachment in rows.all():
            if attachment.message_id is not None:
                grouped.setdefault(attachment.message_id, []).append(attachment)
        return grouped
