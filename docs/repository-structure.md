# Repository Structure

**Status:** Draft for review (Phase 0) · **Last updated:** 2026-08-13

A single monorepo. Shared contracts between the web app, services, and infrastructure are the dominant coupling, and a monorepo keeps them versioned together.

---

## 1. Layout

```text
janus-ai/
├── README.md
├── Makefile                          # dev · test · lint · migrate · seed · up
├── docker-compose.yml                # postgres · redis · api · gateway · web · ollama (optional)
├── .env.example                      # placeholders only, never real values
├── .gitignore
├── .pre-commit-config.yaml
├── pyproject.toml                    # workspace-level Python tooling (ruff, mypy, pytest)
├── package.json                      # workspace-level JS tooling (pnpm workspaces)
│
├── docs/
│   ├── architecture.md
│   ├── model-gateway.md
│   ├── model-routing.md
│   ├── model-registry.md
│   ├── agents.md
│   ├── database.md
│   ├── api.md
│   ├── security.md
│   ├── aws.md
│   ├── observability.md
│   ├── roadmap.md
│   ├── repository-structure.md
│   ├── runbooks/                     # one per alert
│   └── adr/                          # architecture decision records
│
├── apps/
│   └── web/                          # janus-web — Next.js + TypeScript + Tailwind
│       ├── src/app/                  # routes: chat, agents, models, knowledge, settings, admin
│       ├── src/components/           # chat stream, model selector, agent builder, catalog
│       ├── src/lib/                  # API client, SSE handling, auth helpers
│       └── Dockerfile
│
├── services/
│   ├── api/                          # janus-api — control plane + AI Runtime host
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── routers/              # conversations, agents, tools, knowledge, org, policies, usage
│   │   │   ├── domain/               # entities, value objects, invariants
│   │   │   ├── services/             # use cases (no framework imports)
│   │   │   ├── repositories/         # org-scoped data access
│   │   │   ├── auth/                 # sessions, API keys, RBAC
│   │   │   └── deps.py               # dependency injection wiring
│   │   ├── migrations/               # Alembic
│   │   ├── tests/
│   │   └── Dockerfile
│   │
│   ├── gateway/                      # janus-gateway — Model Gateway
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── routers/              # chat_completions, embeddings, models, providers, deployments
│   │   │   ├── pipeline/             # ordered stages: auth → … → observability
│   │   │   ├── router/               # ModelRouter: requirements, filters, scoring, fallback
│   │   │   ├── registry/             # model + deployment registry service, caching
│   │   │   ├── backends/             # ModelBackend implementations
│   │   │   │   ├── base.py
│   │   │   │   ├── openai_compatible.py
│   │   │   │   ├── sarvam.py  openai.py  anthropic.py  gemini.py  bedrock.py
│   │   │   │   ├── vllm.py  sglang.py  ollama.py  llamacpp.py  mlx.py
│   │   │   │   └── mock.py
│   │   │   ├── health/               # probes, state machine, warm-up controller
│   │   │   ├── metering/             # usage records, cost calculation
│   │   │   └── security/             # credential resolver, egress guard
│   │   ├── tests/
│   │   │   └── conformance/          # adapter conformance suite (all backends)
│   │   └── Dockerfile
│   │
│   └── worker/                       # janus-worker — async jobs
│       ├── app/jobs/                 # ingestion, embedding, health_probe, rollup, eval
│       └── Dockerfile
│
├── packages/
│   ├── janus-core/                   # Python: settings, logging, OTel, errors, ids, pagination
│   ├── janus-schemas/                # Python: Pydantic contracts shared by api + gateway + worker
│   ├── janus-runtime/                # Python: LangChain/LangGraph facade, chat + agent graphs, tools, MCP client
│   ├── janus-sdk-python/             # Generated client
│   ├── janus-sdk-ts/                 # Generated client
│   └── ui/                           # Shared React components + design tokens
│
├── registry/                         # Model registry as code
│   ├── providers/*.yaml
│   ├── models/*.yaml
│   ├── licenses/*.yaml
│   └── environments/{local,dev,staging,prod}.yaml
│
├── evals/
│   ├── datasets/                     # versioned eval sets (reasoning, coding, tools, safety, per-language)
│   ├── harness/                      # runner, scorers, rubric judges
│   └── reports/
│
├── infra/
│   └── terraform/                    # modules · environments · bootstrap (see aws.md §8)
│
├── deploy/
│   └── kubernetes/                   # GPU serving only (Phase 8): vLLM, SGLang, controller, CRDs
│
├── tools/
│   ├── seed/                         # local seed data
│   └── scripts/                      # dev utilities
│
└── .github/workflows/                # ci, terraform-plan, deploy, evals, security-scan
```

---

## 2. Boundary rules

Enforced by import-linter (Python) and ESLint boundaries (TypeScript), checked in CI:

| Rule | Enforcement |
|------|-------------|
| Only `services/gateway/app/backends/**` may import a provider SDK | Import-linter forbidden-module rule |
| `services/api` and `packages/janus-runtime` must not import provider SDKs | Same |
| `packages/janus-runtime` reaches models only through the gateway HTTP client | Same |
| `domain/` and `services/` must not import FastAPI or SQLAlchemy | Layered contract |
| `apps/web` must not import server-only packages or read provider secrets | ESLint + env allow-list |
| Shared contracts live in `packages/janus-schemas`; no duplicated request/response models | Review + schema drift test |
| Every tenant table has an RLS policy | Migration test |

These are the mechanical guarantees behind "no business logic depends on a specific model vendor". A pull request that adds `if provider == "sarvam"` outside an adapter fails CI.

---

## 3. Ownership

| Path | Owner |
|------|-------|
| `services/gateway/`, `registry/`, `evals/` | AI Platform / ML Infrastructure |
| `services/api/`, `packages/janus-runtime/` | Backend / Applied AI |
| `apps/web/`, `packages/ui/` | Frontend / Product Design |
| `infra/`, `deploy/`, `.github/workflows/` | DevOps |
| `docs/security.md`, auth code, policy engine | Security Architecture |
| `docs/` (architecture, ADRs) | Principal Architect |

Codified in `CODEOWNERS` at implementation time.

---

## 4. Local development

```bash
make up          # docker compose: postgres, redis, api, gateway, web
make migrate     # alembic upgrade head
make seed        # registry from registry/environments/local.yaml + demo org
make test        # unit + conformance (mock backend only)
make lint        # ruff, mypy, eslint, tsc, import-linter
```

Requirements: no AWS dependency, no paid provider calls in tests, and an end-to-end chat path against Ollama or the mock backend ([model-gateway.md](./model-gateway.md#9-local-development-and-testing)).

---

## 5. Conventions

| Area | Convention |
|------|-----------|
| Python | 3.12, ruff (lint + format), mypy strict on `domain`/`services`/`backends`, pytest, no bare `except` |
| TypeScript | strict mode, no `any` in shared packages, server components by default |
| Commits | Conventional Commits; PR template includes security and performance notes |
| Tests | Unit (fast, no I/O), integration (Docker services), conformance (adapters), property (policy/routing invariants), load (gateway) |
| Docs | Any architectural change ships with an ADR in the same PR |
| Versioning | Services deploy from `main`; SDKs semver from generated OpenAPI |
