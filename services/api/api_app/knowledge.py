"""Knowledge bases: ingest, chunk, embed, retrieve."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from janus_core.errors import ConflictError, NotFoundError, ValidationError
from janus_core.ids import IdPrefix, new_id
from janus_core.logging import get_logger
from janus_schemas.common import Classification, ExecutionMode
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_app.gateway_client import GatewayClient
from api_app.models import KnowledgeBase, KnowledgeDocument

logger = get_logger(__name__)

EMBEDDING_DIMENSIONS = 8
DEFAULT_EMBEDDING_MODEL = "janus/mock-embed"
MAX_CHUNK_CHARS = 800


def hash_embed(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """Deterministic embedding used when the gateway mock (or tests) need a vector.

    Same 8-byte SHA-256 projection the mock backend uses, so retrieval works
    without a network and without fabricating quality claims.
    """
    digest = hashlib.sha256(text.encode()).digest()
    return [(digest[position] / 255.0) * 2 - 1 for position in range(dimensions)]


def chunk_text(body: str) -> list[str]:
    """Script-agnostic chunking: paragraphs first, then length."""
    parts = re.split(r"\n\s*\n", body.strip())
    chunks: list[str] = []
    for part in parts:
        piece = " ".join(part.split())
        if not piece:
            continue
        while len(piece) > MAX_CHUNK_CHARS:
            chunks.append(piece[:MAX_CHUNK_CHARS].rsplit(" ", 1)[0] or piece[:MAX_CHUNK_CHARS])
            piece = piece[len(chunks[-1]) :].lstrip()
        if piece:
            chunks.append(piece)
    return chunks or [body.strip()]


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    content: str
    score: float


class KnowledgeService:
    async def create_base(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        user_id: str,
        name: str,
        description: str | None = None,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> KnowledgeBase:
        existing = await session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.organization_id == organization_id, KnowledgeBase.name == name
            )
        )
        if existing is not None:
            raise ConflictError("A knowledge base with this name already exists.")
        base = KnowledgeBase(
            id=new_id(IdPrefix.KNOWLEDGE_BASE),
            organization_id=organization_id,
            name=name,
            description=description,
            classification=Classification.INTERNAL,
            embedding_model=embedding_model,
            embedding_dimensions=EMBEDDING_DIMENSIONS,
            created_by=user_id,
        )
        session.add(base)
        await session.flush()
        return base

    async def list_bases(self, session: AsyncSession) -> list[KnowledgeBase]:
        result = await session.scalars(
            select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
        )
        return list(result)

    async def get_base(self, session: AsyncSession, knowledge_base_id: str) -> KnowledgeBase:
        base = await session.get(KnowledgeBase, knowledge_base_id)
        if base is None:
            raise NotFoundError("Knowledge base not found.", code="knowledge_base_not_found")
        return base

    async def ingest(
        self,
        session: AsyncSession,
        *,
        base: KnowledgeBase,
        title: str,
        body: str,
        user_id: str | None,
        gateway: GatewayClient | None,
        organization_id: str,
        request_id: str,
        mode: ExecutionMode,
        classification: Classification,
    ) -> KnowledgeDocument:
        if not body.strip():
            raise ValidationError("Document text is required.", param="content")
        digest = hashlib.sha256(body.encode()).hexdigest()
        duplicate = await session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_id == base.id,
                KnowledgeDocument.content_sha256 == digest,
            )
        )
        if duplicate is not None:
            raise ConflictError("This document is already in the knowledge base.")

        document = KnowledgeDocument(
            id=new_id(IdPrefix.DOCUMENT),
            knowledge_base_id=base.id,
            organization_id=base.organization_id,
            title=title,
            source_type="upload",
            mime_type="text/plain",
            size_bytes=len(body.encode()),
            content_sha256=digest,
            classification=base.classification,
            status="embedding",
            created_by=user_id,
        )
        session.add(document)
        await session.flush()

        pieces = chunk_text(body)
        vectors = await self._embed(
            pieces,
            gateway=gateway,
            model=base.embedding_model,
            organization_id=organization_id,
            request_id=request_id,
            mode=mode,
            classification=classification,
            dimensions=base.embedding_dimensions,
        )
        for index, (piece, vector) in enumerate(zip(pieces, vectors, strict=True), start=1):
            await session.execute(
                text(
                    """
                    INSERT INTO knowledge.chunks (
                      id, document_id, knowledge_base_id, organization_id, sequence,
                      content, token_count, embedding, embedding_model, embedding_version, metadata
                    ) VALUES (
                      :id, :document_id, :knowledge_base_id, :organization_id, :sequence,
                      :content, :token_count, CAST(:embedding AS vector),
                      :embedding_model, '1', '{}'::jsonb
                    )
                    """
                ),
                {
                    "id": new_id(IdPrefix.CHUNK),
                    "document_id": document.id,
                    "knowledge_base_id": base.id,
                    "organization_id": base.organization_id,
                    "sequence": index,
                    "content": piece,
                    "token_count": max(1, len(piece.split())),
                    "embedding": _vector_literal(vector),
                    "embedding_model": base.embedding_model,
                },
            )

        document.status = "ready"
        document.chunk_count = len(pieces)
        document.updated_at = datetime.now(UTC)
        await session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == base.id)
            .values(document_count=KnowledgeBase.document_count + 1)
        )
        return document

    async def retrieve(
        self,
        session: AsyncSession,
        *,
        knowledge_base_id: str,
        query: str,
        limit: int = 4,
        embedding_model: str,
        dimensions: int,
        gateway: GatewayClient | None = None,
        organization_id: str = "",
        request_id: str = "",
        mode: ExecutionMode = ExecutionMode.AUTO,
        classification: Classification = Classification.INTERNAL,
    ) -> list[RetrievedChunk]:
        vectors = await self._embed(
            [query],
            gateway=gateway,
            model=embedding_model,
            organization_id=organization_id,
            request_id=request_id,
            mode=mode,
            classification=classification,
            dimensions=dimensions,
        )
        rows = await session.execute(
            text(
                """
                SELECT id, document_id, content,
                       (1 - (embedding <=> CAST(:query AS vector))) AS score
                FROM knowledge.chunks
                WHERE knowledge_base_id = :kb
                ORDER BY embedding <=> CAST(:query AS vector)
                LIMIT :limit
                """
            ),
            {"query": _vector_literal(vectors[0]), "kb": knowledge_base_id, "limit": limit},
        )
        return [
            RetrievedChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                content=row.content,
                score=float(row.score or 0),
            )
            for row in rows
        ]

    async def _embed(
        self,
        texts: list[str],
        *,
        gateway: GatewayClient | None,
        model: str,
        organization_id: str,
        request_id: str,
        mode: ExecutionMode,
        classification: Classification,
        dimensions: int,
    ) -> list[list[float]]:
        if gateway is not None:
            try:
                _status, payload = await gateway.embeddings(
                    {"model": model, "input": texts},
                    organization_id=organization_id,
                    request_id=request_id,
                    mode=mode,
                    classification=classification,
                )
                data = payload.get("data") or []
                if len(data) == len(texts):
                    return [item["embedding"][:dimensions] for item in data]
            except Exception as exc:
                logger.info("gateway_embed_fallback", extra={"error": type(exc).__name__})
        return [hash_embed(text, dimensions) for text in texts]
