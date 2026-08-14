# Janus Intelligence

**One AI interface. Every model. Every deployment.**

Janus Intelligence is an AI operating platform for **conversations, agents, models, knowledge and workflows**. The launch market is the **United States**: mass-market chat and agents backed by frontier cloud models, with private and self-hosted open-source models for teams that cannot send data to a third party.

Multilingual capability — including strong Indic-language support through Sarvam — is a differentiator layered on top, not the entry point.

Website: <https://www.janus-intelligence.ai>

> **Status: Phases 0–10 shipped as working slices.**
> Local product: chat, catalog, agents, knowledge, usage. AWS: Terraform in
> [`infra/aws`](infra/aws) and [aws-deploy.md](docs/aws-deploy.md). Marketplace:
> [marketplace.md](docs/marketplace.md) (seller verification is AWS-gated; not a
> next-week guarantee). As-built notes: [phase-4](docs/phase-4.md) …
> [phase-10](docs/phase-10.md).

---

## Run it locally

Needs Docker, and — for working outside containers — Python 3.12+ with [uv](https://docs.astral.sh/uv/) and Node 20.12+ (22 matches CI). If the distro Node is older, `make node` installs 22 under `~/.local/node-v22` and Make uses it automatically.

On this DGX host, activate the shared Python 3.12 environment with your `venv` alias first (`dgx-ai-lab`), then bootstrap host tools:

```bash
venv                 # Python 3.12 from dgx-ai-lab
./install.sh         # Terraform, AWS CLI, gh, Node 22, uv sync, npm
# or: make bootstrap
```

```bash
make env          # create .env from the example
make stack-up     # postgres, migrations, gateway, api, web
make smoke-chat   # log in, create a conversation, stream a mock reply
make smoke-product # knowledge ingest/search + agent run + /v1/responses
```

Open the web URL `make stack-up` prints (port 3000 unless `.env` overrides it), create a workspace, and send a message. Out of the box it answers from a deterministic mock model, so the whole path is verifiable with no API key and no GPU.

If ports 3000, 8080, 8081, or 5432 are already taken, override them — nothing inside the stack depends on the published numbers:

```bash
JANUS_WEB_PORT=3010 JANUS_API_PORT=8090 make stack-up
```

### Using it from another machine

The browser needs **only the web port**. The web server proxies `/api/*` to the control plane itself, so nothing in the page refers to the API's host or port — over an SSH tunnel, a forwarded port, or a private network address, it works with no reconfiguration.

```bash
# Forward one port over SSH, then open http://localhost:3000 locally.
ssh -L 3000:localhost:3000 you@host
```

To reach it over a private network (Tailscale, VPN, WireGuard) instead, pin the published ports to that interface so the stack is not exposed to the local network:

```bash
JANUS_BIND_ADDRESS=100.x.y.z make stack-up   # then http://100.x.y.z:3000
```

There is no TLS in the local stack. Reach it over a tunnel or a private network, not the open internet.

To answer from a real local model instead, pull one with Ollama and restart the gateway:

```bash
ollama pull llama3.1:8b
```

For development with reload, run the pieces directly:

```bash
make install
make db-up migrate
make run-gateway   # :8081
make run-api       # :8080
make run-web       # :3000
```

`make check` runs everything CI runs: lint, types, architectural boundaries, web checks, and tests. `make help` lists the rest.

---

## Local vs AWS (where containers run)

| | **Local (now)** | **AWS (after deploy)** |
|--|-----------------|-------------------------|
| **Host** | This machine (e.g. DGX Spark) via Docker Compose | Your AWS account |
| **web / api / gateway** | Compose containers on the host | **ECS Fargate** tasks (serverless containers) |
| **Postgres / Redis** | Compose services on the host | Aurora PostgreSQL + ElastiCache |
| **How you start it** | `make stack-up` | `terraform apply` in [`infra/aws`](infra/aws), then push images ([aws-deploy.md](docs/aws-deploy.md)) |
| **How you reach it** | e.g. `http://localhost:3010` (ports from `.env`) | ALB DNS (CloudWatch logs under `/ecs/janus-<env>/…`) |

**Today the stack is not on Fargate.** `api`, `gateway`, and `web` are ordinary Docker containers on the local host. Fargate is the production/staging runtime defined in Terraform; it exists only after you apply that stack and deploy images to ECR.

Same application code and images in both places — only the orchestrator changes (Compose vs ECS).

Troubleshooting: [docs/runbooks/troubleshooting.md](docs/runbooks/troubleshooting.md). Architecture / scaling: [docs/architecture.md](docs/architecture.md#23-serverless-compute--and-how-it-scales-under-load).

---

## What this build delivers

| Area | State |
|------|-------|
| Chat | Persisted conversations, SSE, catalog, Auto routing |
| Gateway | OpenAI-compatible `/v1/*`, `jsk_` keys, mock + Ollama + cloud adapters + vLLM/SGLang/llama.cpp/MLX |
| Agents | Versioned agents, checkpointed retrieve/tool/compose loop, `/v1/responses` |
| Knowledge | Text ingest, pgvector retrieve, citations on agent runs |
| Ops | Usage totals, audit list, org policies, deployment health (no endpoints) |
| Web | Chat, Models, Agents, Knowledge, Usage |
| AWS | Terraform in `infra/aws`; runbook [aws-deploy.md](docs/aws-deploy.md) |
| Marketplace | Preparation guide [marketplace.md](docs/marketplace.md) — seller approval is AWS-gated |

Honest gaps: no live GPU fleet, no SSO/SOC 2, no LangGraph product, no PDF parsers, Marketplace listing is not submitted from this repo.

---

## Core idea

```text
Chat + Agents  →  AI Runtime (LangChain / LangGraph)  →  Model Gateway  →  Intelligent Router
                                                                              │
                    ┌─────────────────────────────┬─────────────────────────┐
                Cloud models                 Janus-hosted               Local / edge
             Sarvam, OpenAI,               vLLM, SGLang on            Ollama, llama.cpp,
           Anthropic, Gemini, Bedrock        GPU clusters                   MLX
```

Two principles drive every decision in these documents:

1. **Model provider independence.** No business logic branches on a vendor name. Providers are adapters behind a stable interface, so Sarvam, OpenAI, or a self-hosted Llama are interchangeable deployment details.
2. **The Model Gateway is a security and policy boundary.** Authentication, tenant isolation, data classification, policy enforcement, routing, metering, and observability all happen there — not scattered through feature code.

---

## Design documents

Start with [phase-4.md](docs/phase-4.md)–[phase-10.md](docs/phase-10.md) for what this
increment actually built, and [phase-3.md](docs/phase-3.md) for the gateway.

| # | Document | Contents |
|---|----------|----------|
| 1 | [architecture.md](docs/architecture.md) | System architecture, domain model, component boundaries, data flow |
| 2 | [model-gateway.md](docs/model-gateway.md) | Gateway internals, backend abstraction, health, warming, local serving |
| 3 | [model-routing.md](docs/model-routing.md) | Capability system, scoring, routing policies, fallback, Auto mode |
| 4 | [model-registry.md](docs/model-registry.md) | Model + deployment registry schema, onboarding, license compliance |
| 5 | [agents.md](docs/agents.md) | Runtime, agent policies, tools (LangGraph remains the design; Phase 5 is a loop) |
| 6 | [database.md](docs/database.md) | PostgreSQL schema, multi-tenancy, RLS, migrations |
| 7 | [api.md](docs/api.md) | Public API contract (OpenAI-compatible + Janus control plane) |
| 8 | [security.md](docs/security.md) | Auth, RBAC, tenant isolation, data classification, policy engine |
| 9 | [aws.md](docs/aws.md) | Hybrid ECS + EKS infrastructure |
| 10 | [observability.md](docs/observability.md) | OpenTelemetry, routing decision logs, metering, evaluation |
| 11 | [roadmap.md](docs/roadmap.md) | Phases 1–10 with exit criteria |
| — | [aws-deploy.md](docs/aws-deploy.md) | Apply Terraform to your AWS account |
| — | [marketplace.md](docs/marketplace.md) | AWS Marketplace seller + listing prep |
| — | [sales.md](docs/sales.md) | Who to sell to, packaging, demo, objections |
| — | [air-gapped.md](docs/air-gapped.md) | Offline / air-gapped offer and delivery |
| — | [phase-1.md](docs/phase-1.md) … [phase-10.md](docs/phase-10.md) | As-built notes |
| — | [adr/](docs/adr/) | Architecture decision records |

---

## What is deliberately **not** decided yet

Recorded so reviewers can push back before code exists:

- Vector store choice (pgvector vs. OpenSearch vs. dedicated) — [database.md](docs/database.md#8-vector-storage-decision-pending)
- Whether the AI Runtime is a library inside `janus-api` or a separate service — [architecture.md](docs/architecture.md#43-runtime-deployment-options)
- Billing/pricing model and cost attribution granularity — [observability.md](docs/observability.md#6-usage-metering)
- Voice pipeline provider set — deferred to Phase 10

## Assumptions requiring verification

These come from the product brief and must be confirmed against provider documentation and contracts **before** Phase 2 implementation. They are treated as assumptions, not facts, throughout the docs.

| Assumption | Why it matters | Verify |
|-----------|----------------|--------|
| Frontier provider context windows, pricing, rate limits (OpenAI, Anthropic, Google, Bedrock) | Routing cost/latency classes, US launch economics | Provider pricing pages + contracts |
| Sarvam offers ~30B and ~105B chat models via API | Registry seed data for the multilingual tier | Sarvam API docs + contract |
| Sarvam open weights can be self-hosted on vLLM/SGLang | "Sarvam Local" deployment target | Model license terms |
| Sarvam speech/translation models available | Phase 10 voice design | Provider docs |
| Open-weight model licenses permit commercial hosting | Janus-hosted private tier | Per-model license review |

No benchmark numbers appear in these documents. Janus publishes **only** figures measured by its own evaluation harness ([observability.md](docs/observability.md#7-evaluation--benchmarking)).

---

## Development method

Delivery is phase-gated:

```text
Phase 0  Design  ✓  →  Phase 1  Foundation  ✓  →  Phase 2  Chat  ✓  →  Phase 3  Gateway  ✓  →  …
```

Each implementation phase must ship: objective, architectural decisions, files created/modified, implementation, tests, security review, performance review, documentation.

The full phase plan with exit criteria is in [roadmap.md](docs/roadmap.md).

---

## Engineering rules (non-negotiable)

**Never:** hard-code secrets, providers, or AWS resources · expose chain-of-thought · trust client-side authorization · commit secrets · add microservices or Kubernetes without justification · bypass migrations or tests · fabricate benchmarks · make unsupported privacy claims.

**Always:** typed interfaces · dependency injection · structured logging · OpenTelemetry · migrations · automated tests · Terraform · least-privilege IAM · Secrets Manager · provider abstraction · tenant isolation · rate limiting · retries with timeouts · record AI usage, latency, and model selection · document decisions as ADRs.

---

## Review

Open questions are collected at the end of each document under **Open questions**; the design documents remain the place to argue about direction before code follows.

---

*Related internal work: [`local-models`](../local-models/README.md) (Ollama serving on DGX Spark) and [`local-chat`](../local-chat/README.md) (LangChain gateway + Next.js chat) are precursors; Janus generalizes both.*
