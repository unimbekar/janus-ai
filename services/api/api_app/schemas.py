"""Request and response bodies for the platform API."""

from __future__ import annotations

from datetime import datetime

from janus_schemas.common import Classification, ExecutionMode
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    name: str | None = Field(default=None, max_length=200)
    organization_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None = None
    email_verified: bool = False


class OrganizationResponse(BaseModel):
    id: str
    slug: str
    name: str
    plan: str
    default_mode: ExecutionMode
    default_classification: Classification
    role: str | None = None
    created_at: datetime


class SessionResponse(BaseModel):
    user: UserResponse
    organization: OrganizationResponse
    organizations: list[OrganizationResponse] = Field(default_factory=list)


class SwitchOrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str


class UpdateOrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    default_mode: ExecutionMode | None = None
    default_classification: Classification | None = None


class CreateOrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class AddMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: str = "member"


class MemberResponse(BaseModel):
    user_id: str
    email: str
    name: str | None
    role: str
    joined_at: datetime


class CreateApiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["inference:read", "inference:write"])
    mode_ceiling: ExecutionMode | None = None


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    mode_ceiling: ExecutionMode | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class CreatedApiKeyResponse(ApiKeyResponse):
    """The only response that ever contains the key itself."""

    key: str = Field(description="Shown once. Janus stores only a hash.")


# ------------------------------------------------------------------ chat


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    #: A model slug to use for every turn, or omitted to route per message.
    pinned_model: str | None = Field(default=None, max_length=200)
    #: May only narrow the organization default, never widen it.
    mode: ExecutionMode | None = None


class UpdateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    pinned_model: str | None = Field(default=None, max_length=200)
    #: Explicit, because ``pinned_model: null`` cannot be told apart from an
    #: absent field once the body is parsed.
    clear_pinned_model: bool = False
    mode: ExecutionMode | None = None


class AttachmentResponse(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    scan_status: str
    created_at: datetime


class MessageResponse(BaseModel):
    id: str
    role: str
    sequence: int
    content: str
    status: str
    model: str | None = None
    deployment: str | None = None
    provider: str | None = None
    privacy: str | None = None
    fallback_used: bool = False
    routing_explanation: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    error: dict | None = None
    parent_message_id: str | None = None
    attachments: list[AttachmentResponse] = Field(default_factory=list)
    created_at: datetime


class ConversationResponse(BaseModel):
    id: str
    title: str | None
    pinned_model: str | None
    mode: ExecutionMode | None
    message_count: int
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConversationDetailResponse(ConversationResponse):
    messages: list[MessageResponse] = Field(default_factory=list)


class ConversationPageResponse(BaseModel):
    data: list[ConversationResponse]
    next_cursor: str | None = None


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=200_000)
    #: Overrides the conversation's pinned model for this turn only.
    model: str | None = Field(default=None, max_length=200)
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)


class CancelResponse(BaseModel):
    cancelled: int = Field(description="How many in-flight generations were signalled.")
