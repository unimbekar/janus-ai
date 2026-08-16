# Repository Structure

**Status:** as-built notes · **Last updated:** 2026-08-16

A single monorepo. Shared contracts between the web app, services, and infrastructure are the dominant coupling, and a monorepo keeps them versioned together.

---

## 1. Layout

```text
janus-ai/
├── README.md
├── Makefile
├── setup.sh                          # customer wizard: local / AWS / tools
├── install.sh                        # tools + start/stop/status/ensure-models
├── docker-compose.yml                # postgres · redis · api · gateway · web
├── .env.example
├── config/
│   └── local-models.yaml             # Ollama tags for local ensure/pull
├── docs/
│   ├── aws-deploy.md                 # apply Terraform + push images
│   ├── aws.md
│   ├── runbooks/troubleshooting.md
│   └── …
├── apps/web/
├── services/{api,gateway}/
├── packages/
├── registry/
│   ├── models/*.yaml
│   └── environments/{local,dev,staging,prod}.yaml
└── infra/
    └── aws/                          # Phase 7 ECS / Aurora / Redis (see aws-deploy.md)
```

Legacy target layout (`infra/terraform/modules/…`) is described in [aws.md](./aws.md) §8 as a future split; **deploy today from `infra/aws/`**.

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
./install.sh              # tools + workspace deps
./install.sh start        # Ollama ensure + compose stack
./install.sh ensure-models
./install.sh status
./install.sh stop
make migrate              # alembic (host) against local Postgres
make test
make check                # full CI suite
```

Or the customer wizard: `./setup.sh --local --yes`.

AWS: `./setup.sh --aws` then [aws-deploy.md](./aws-deploy.md).

Requirements: no AWS dependency for the local happy path, no paid provider calls in default tests, and an end-to-end chat path against the mock backend (or Ollama from `config/local-models.yaml`).

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
