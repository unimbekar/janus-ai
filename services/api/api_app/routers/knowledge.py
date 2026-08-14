"""Knowledge bases and document ingestion."""

from __future__ import annotations

from fastapi import APIRouter, status
from janus_core.errors import AuthorizationError
from janus_core.ids import IdPrefix, new_id
from pydantic import BaseModel, ConfigDict, Field

from api_app.deps import (
    ClassificationDep,
    GatewayDep,
    ModeDep,
    PrincipalDep,
    RequestIdDep,
    SessionDep,
)
from api_app.knowledge import KnowledgeService
from api_app.models import KnowledgeBase, KnowledgeDocument

router = APIRouter(prefix="/v1/knowledge-bases", tags=["knowledge"])
knowledge = KnowledgeService()


class CreateKnowledgeBaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    embedding_model: str = "janus/mock-embed"


class IngestDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=500_000)


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    description: str | None
    embedding_model: str
    document_count: int
    created_at: object


class DocumentResponse(BaseModel):
    id: str
    title: str | None
    status: str
    chunk_count: int
    content_sha256: str | None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=4, ge=1, le=20)


def _base(base: KnowledgeBase) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        id=base.id,
        name=base.name,
        description=base.description,
        embedding_model=base.embedding_model,
        document_count=base.document_count,
        created_at=base.created_at,
    )


def _doc(document: KnowledgeDocument) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        title=document.title,
        status=document.status,
        chunk_count=document.chunk_count,
        content_sha256=document.content_sha256,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    body: CreateKnowledgeBaseRequest,
    principal: PrincipalDep,
    session: SessionDep,
) -> KnowledgeBaseResponse:
    principal.require_role("member")
    if principal.user_id is None:
        raise AuthorizationError("Creating a knowledge base requires a signed-in user.")
    user_id = principal.user_id
    base = await knowledge.create_base(
        session,
        organization_id=principal.organization_id,
        user_id=user_id,
        name=body.name,
        description=body.description,
        embedding_model=body.embedding_model,
    )
    return _base(base)


@router.get("")
async def list_knowledge_bases(
    principal: PrincipalDep, session: SessionDep
) -> dict[str, list[KnowledgeBaseResponse]]:
    bases = await knowledge.list_bases(session)
    return {"data": [_base(item) for item in bases]}


@router.get("/{knowledge_base_id}")
async def get_knowledge_base(
    knowledge_base_id: str, principal: PrincipalDep, session: SessionDep
) -> KnowledgeBaseResponse:
    return _base(await knowledge.get_base(session, knowledge_base_id))


@router.post("/{knowledge_base_id}/documents", status_code=status.HTTP_201_CREATED)
async def ingest_document(
    knowledge_base_id: str,
    body: IngestDocumentRequest,
    principal: PrincipalDep,
    session: SessionDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> DocumentResponse:
    base = await knowledge.get_base(session, knowledge_base_id)
    document = await knowledge.ingest(
        session,
        base=base,
        title=body.title,
        body=body.content,
        user_id=principal.user_id,
        gateway=gateway,
        organization_id=principal.organization_id,
        request_id=request_id or new_id(IdPrefix.REQUEST),
        mode=mode,
        classification=classification,
    )
    return _doc(document)


@router.post("/{knowledge_base_id}/search")
async def search(
    knowledge_base_id: str,
    body: SearchRequest,
    principal: PrincipalDep,
    session: SessionDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> dict:
    base = await knowledge.get_base(session, knowledge_base_id)
    hits = await knowledge.retrieve(
        session,
        knowledge_base_id=base.id,
        query=body.query,
        limit=body.limit,
        embedding_model=base.embedding_model,
        dimensions=base.embedding_dimensions,
        gateway=gateway,
        organization_id=principal.organization_id,
        request_id=request_id,
        mode=mode,
        classification=classification,
    )
    return {
        "data": [
            {
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "content": hit.content,
                "score": hit.score,
            }
            for hit in hits
        ]
    }
