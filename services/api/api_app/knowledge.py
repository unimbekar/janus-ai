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
from api_app.models import Citation, KnowledgeBase, KnowledgeDocument

logger = get_logger(__name__)

EMBEDDING_DIMENSIONS = 8
DEFAULT_EMBEDDING_MODEL = "janus/mock-embed"
MAX_CHUNK_CHARS = 800
CHAT_RETRIEVE_LIMIT = 8
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "what",
    "does",
    "how",
    "are",
    "this",
    "that",
    "from",
    "about",
    "into",
    "when",
    "where",
    "which",
    "your",
    "can",
    "could",
    "would",
    "should",
    "please",
    "explain",
    "tell",
}

RAG_INSTRUCTIONS = (
    "Answer using the retrieved knowledge-base context below. "
    "If the context does not contain the answer, say you could not find it in the "
    "uploaded documents. Do not invent citations or rely on outside knowledge "
    "unless the user explicitly asks you to."
)


def lexical_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-.]{2,}", query)
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        key = token.lower()
        if key in _STOPWORDS or key in seen:
            continue
        seen.add(key)
        out.append(token)
        if len(out) >= 12:
            break
    return out


def ground_messages(
    prompt: list[dict[str, str]], hits: list[RetrievedChunk]
) -> list[dict[str, str]]:
    if not hits:
        return prompt
    blocks = [f"[{index}] {hit.content}" for index, hit in enumerate(hits, start=1)]
    context = RAG_INSTRUCTIONS + "\n\nRetrieved context:\n" + "\n\n".join(blocks)
    return [{"role": "system", "content": context}, *prompt]


def _like_pattern(token: str) -> str:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
        mime_type: str = "text/plain",
        size_bytes: int | None = None,
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
            mime_type=mime_type,
            size_bytes=size_bytes if size_bytes is not None else len(body.encode()),
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
        """Hybrid retrieve: lexical first (FTS / token match), then vector fill.

        The local mock embedder is an 8-dimension hash, so a question about a
        paper will not land near the paper's chunks by cosine alone. Keyword
        search is what makes RAG usable until a real embedding model is pinned.
        """
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
        vector_hits = await self._vector_hits(
            session, knowledge_base_id, vectors[0], limit=limit
        )
        lexical_hits = await self._lexical_hits(session, knowledge_base_id, query, limit=limit)
        merged: list[RetrievedChunk] = []
        seen: set[str] = set()
        for hit in lexical_hits + vector_hits:
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            merged.append(hit)
            if len(merged) >= limit:
                break
        return merged

    async def retrieve_for_organization(
        self,
        session: AsyncSession,
        *,
        query: str,
        gateway: GatewayClient | None = None,
        organization_id: str = "",
        request_id: str = "",
        mode: ExecutionMode = ExecutionMode.AUTO,
        classification: Classification = Classification.INTERNAL,
        limit: int = CHAT_RETRIEVE_LIMIT,
    ) -> list[RetrievedChunk]:
        bases = await self.list_bases(session)
        if not bases:
            return []
        hits: list[RetrievedChunk] = []
        for base in bases:
            hits.extend(
                await self.retrieve(
                    session,
                    knowledge_base_id=base.id,
                    query=query,
                    limit=limit,
                    embedding_model=base.embedding_model,
                    dimensions=base.embedding_dimensions,
                    gateway=gateway,
                    organization_id=organization_id,
                    request_id=request_id,
                    mode=mode,
                    classification=classification,
                )
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        unique: list[RetrievedChunk] = []
        seen: set[str] = set()
        for hit in hits:
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            unique.append(hit)
            if len(unique) >= limit:
                break
        return unique

    async def persist_citations(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        message_id: str,
        hits: list[RetrievedChunk],
    ) -> list[dict]:
        payload: list[dict] = []
        for hit in hits:
            quote = hit.content[:240]
            score = round(min(max(hit.score, 0.0), 0.99999), 5)
            session.add(
                Citation(
                    id=new_id(IdPrefix.CITATION),
                    message_id=message_id,
                    organization_id=organization_id,
                    chunk_id=hit.chunk_id,
                    document_id=hit.document_id,
                    quote=quote,
                    score=score,
                )
            )
            payload.append(
                {
                    "chunk_id": hit.chunk_id,
                    "document_id": hit.document_id,
                    "quote": quote,
                    "score": score,
                }
            )
        return payload

    async def citations_for(
        self, session: AsyncSession, message_ids: list[str]
    ) -> dict[str, list[dict]]:
        if not message_ids:
            return {}
        rows = await session.scalars(
            select(Citation).where(Citation.message_id.in_(message_ids))
        )
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            if row.message_id is None:
                continue
            grouped.setdefault(row.message_id, []).append(
                {
                    "chunk_id": row.chunk_id,
                    "document_id": row.document_id,
                    "quote": row.quote,
                    "score": float(row.score) if row.score is not None else None,
                }
            )
        return grouped

    async def _vector_hits(
        self,
        session: AsyncSession,
        knowledge_base_id: str,
        vector: list[float],
        *,
        limit: int,
    ) -> list[RetrievedChunk]:
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
            {"query": _vector_literal(vector), "kb": knowledge_base_id, "limit": limit},
        )
        return [_row_to_chunk(row) for row in rows]

    async def _lexical_hits(
        self,
        session: AsyncSession,
        knowledge_base_id: str,
        query: str,
        *,
        limit: int,
    ) -> list[RetrievedChunk]:
        fts = await session.execute(
            text(
                """
                SELECT id, document_id, content,
                       ts_rank_cd(to_tsvector('simple', content), q) AS score
                FROM knowledge.chunks, plainto_tsquery('simple', :text) AS q
                WHERE knowledge_base_id = :kb
                  AND length(q::text) > 0
                  AND to_tsvector('simple', content) @@ q
                ORDER BY score DESC
                LIMIT :limit
                """
            ),
            {"text": query, "kb": knowledge_base_id, "limit": limit},
        )
        hits = [_row_to_chunk(row) for row in fts]
        if hits:
            return hits

        tokens = lexical_tokens(query)
        if not tokens:
            return []
        params: dict[str, object] = {"kb": knowledge_base_id, "limit": limit}
        for index in range(12):
            params[f"t{index}"] = _like_pattern(tokens[index]) if index < len(tokens) else ""
        substring = await session.execute(
            text(
                """
                SELECT id, document_id, content, 0.65 AS score
                FROM knowledge.chunks
                WHERE knowledge_base_id = :kb
                  AND (
                    (:t0 <> '' AND content ILIKE :t0 ESCAPE '\\')
                    OR (:t1 <> '' AND content ILIKE :t1 ESCAPE '\\')
                    OR (:t2 <> '' AND content ILIKE :t2 ESCAPE '\\')
                    OR (:t3 <> '' AND content ILIKE :t3 ESCAPE '\\')
                    OR (:t4 <> '' AND content ILIKE :t4 ESCAPE '\\')
                    OR (:t5 <> '' AND content ILIKE :t5 ESCAPE '\\')
                    OR (:t6 <> '' AND content ILIKE :t6 ESCAPE '\\')
                    OR (:t7 <> '' AND content ILIKE :t7 ESCAPE '\\')
                    OR (:t8 <> '' AND content ILIKE :t8 ESCAPE '\\')
                    OR (:t9 <> '' AND content ILIKE :t9 ESCAPE '\\')
                    OR (:t10 <> '' AND content ILIKE :t10 ESCAPE '\\')
                    OR (:t11 <> '' AND content ILIKE :t11 ESCAPE '\\')
                  )
                LIMIT :limit
                """
            ),
            params,
        )
        return [_row_to_chunk(row) for row in substring]

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


def _row_to_chunk(row: object) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=row.id,  # type: ignore[attr-defined]
        document_id=row.document_id,  # type: ignore[attr-defined]
        content=row.content,  # type: ignore[attr-defined]
        score=float(row.score or 0),  # type: ignore[attr-defined]
    )
