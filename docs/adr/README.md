# Architecture Decision Records

One record per architectural decision. Format: context, decision, consequences, alternatives.

| # | Decision | Status |
|---|----------|--------|
| [0001](./0001-gateway-sole-inference-path.md) | Model Gateway is the sole inference path and a security boundary | Proposed |
| [0002](./0002-openai-compatible-protocol.md) | OpenAI-compatible protocol as internal and external lingua franca | Proposed |
| [0003](./0003-hybrid-ecs-eks.md) | Hybrid ECS (core) + EKS (GPU only) | Proposed |
| [0004](./0004-ai-runtime-boundary.md) | AI Runtime starts as a library inside `janus-api` | Proposed |
| [0005](./0005-multi-tenancy-rls.md) | Shared-schema multi-tenancy with PostgreSQL row-level security | Proposed |
| [0006](./0006-pgvector-for-retrieval.md) | Aurora PostgreSQL + pgvector for Phase 6 retrieval | Proposed |
| [0007](./0007-platform-scoped-registry.md) | Platform-scoped model registry with per-organization visibility | Proposed |

**Status values:** Proposed → Accepted → Superseded (by #N) → Deprecated.

Rules: an architectural change ships with an ADR in the same pull request; superseding a record links both directions; records are never edited to hide history — they are superseded.
