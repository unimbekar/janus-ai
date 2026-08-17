# Phase 6 — As Built

**Status:** complete for text RAG · **Last updated:** 2026-08-14

One sentence: documents go in as text, chunks and embeddings live in pgvector
behind RLS, search returns scored chunks, agents can cite them.

## What shipped

- Schema `knowledge.*` + `chat.citations`. Embedding column is `vector(8)` to
  match the mock embedder (not a claim of production embedding quality).
- Ingest: paragraph-then-length chunking, SHA-256 dedupe, embed via gateway with
  hash fallback when the stub/gateway returns no vectors.
- Retrieve: cosine distance in pgvector, org-scoped by RLS.
- Web: `/knowledge` create, paste ingest, multi-file upload (txt/md/csv/json/html/pdf/docx), search.
- Agents with `knowledge_search` attach citations on the run.

## Honest deferrals

- OCR for scanned PDFs, hybrid lexical+vector search, reranking, and async
  workers are not in this slice.
- Mixed embedding-dimension tables are not implemented; dimension is pinned at 8
  for the mock path.
