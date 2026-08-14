# ADR 0006 — Aurora PostgreSQL + pgvector for Phase 6 retrieval

**Status:** Accepted · **Date:** 2026-08-13 · **Deciders:** Principal Architect, ML Infrastructure Engineer

## Context

Phase 6 introduces RAG: documents, chunks, embeddings, and similarity search. Choices are pgvector inside the existing Aurora cluster, OpenSearch, or a dedicated vector database.

Retrieval must respect tenant isolation and document-level classification, and chunk metadata is relational (document, knowledge base, organization, page, heading path). A separate vector store means enforcing tenancy twice, in two different mechanisms, and keeping metadata synchronized.

## Decision

Use **pgvector in Aurora PostgreSQL** with HNSW indexes for Phase 6, behind a `Retriever` interface that leaks no SQL to callers.

Additional constraints: the embedding model and version are recorded on every chunk; searching across mixed embedding versions is refused; changing a knowledge base's embedding model triggers a re-embedding job.

Revisit when either threshold is crossed: roughly 10 million chunks for a single organization, or p95 retrieval latency above 300 ms with tuned indexes.

## Consequences

**Positive:** one datastore, so RLS covers vectors with the same mechanism as everything else; transactional consistency between chunks and their metadata; hybrid search combines HNSW with PostgreSQL full-text in one query; no new vendor or ops surface during Phase 6.

**Negative:** pgvector requires a fixed dimension per column, so multiple embedding dimensions need per-dimension tables or a normalization decision (open question in [database.md](../database.md#13-open-questions)); very large corpora will eventually outgrow it; index builds consume database resources that also serve chat, so ingestion runs on workers with throttling.

**Neutral:** the `Retriever` interface makes a later migration a contained change rather than a rewrite.

## Alternatives considered

| Alternative | Why rejected for Phase 6 |
|-------------|--------------------------|
| OpenSearch | Mature hybrid search and scale, but a second datastore with its own tenancy enforcement and sync burden before we have evidence we need it |
| Dedicated vector database | Best raw vector performance; adds a vendor, a second tenancy model, and cost, for a scale we have not reached |
| Provider-managed retrieval (e.g. vendor file search) | Contradicts provider independence and private/sovereign deployment guarantees |
