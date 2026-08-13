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
