# Janus Intelligence

**One AI interface. Every model. Every deployment.**

Janus Intelligence is an AI operating platform for **conversations, agents, models, knowledge and workflows** — with first-class support for Indian languages, sovereign AI, and open-source models running on Janus-controlled infrastructure.

Website: <https://www.janus-intelligence.ai>

> **Status: DESIGN REVIEW — Phase 0.**
> This repository currently contains **architecture and specification only**. No application code has been written yet, by design (see [Development method](#development-method)). Implementation begins after these documents are reviewed and approved.

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

Read in this order.

| # | Document | Contents |
|---|----------|----------|
| 1 | [architecture.md](docs/architecture.md) | System architecture, domain model, component boundaries, data flow |
| 2 | [model-gateway.md](docs/model-gateway.md) | Gateway internals, backend abstraction, health, warming, local serving |
| 3 | [model-routing.md](docs/model-routing.md) | Capability system, scoring, routing policies, fallback, Auto mode |
| 4 | [model-registry.md](docs/model-registry.md) | Model + deployment registry schema, onboarding, license compliance |
| 5 | [agents.md](docs/agents.md) | LangGraph runtime, agent policies, tools, MCP, marketplace |
| 6 | [database.md](docs/database.md) | PostgreSQL schema, multi-tenancy, RLS, migrations |
| 7 | [api.md](docs/api.md) | Public API contract (OpenAI-compatible + Janus control plane) |
| 8 | [security.md](docs/security.md) | Auth, RBAC, tenant isolation, data classification, policy engine |
| 9 | [aws.md](docs/aws.md) | Hybrid ECS + EKS infrastructure, Terraform layout, GPU pools |
| 10 | [observability.md](docs/observability.md) | OpenTelemetry, routing decision logs, metering, evaluation |
| 11 | [roadmap.md](docs/roadmap.md) | Phases 1–10 with exit criteria |
| — | [repository-structure.md](docs/repository-structure.md) | Proposed monorepo layout and ownership |
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
| Sarvam offers ~30B and ~105B chat models via API | Model registry seed data, routing tiers | Sarvam API docs + contract |
| Sarvam open weights can be self-hosted on vLLM/SGLang | "Sarvam Local" deployment target | Model license terms |
| Sarvam speech/translation models available | Phase 10 voice design | Provider docs |
| Context windows, pricing, rate limits per provider | Routing cost/latency classes | Provider pricing pages |

No benchmark numbers appear in these documents. Janus publishes **only** figures measured by its own evaluation harness ([observability.md](docs/observability.md#7-evaluation--benchmarking)).

---

## Development method

Per the platform brief, delivery is phase-gated:

```text
Phase 0  Design (this repo state)  →  REVIEW GATE  →  Phase 1 …
```

Each implementation phase must ship: objective, architectural decisions, files created/modified, implementation, tests, security review, performance review, documentation.

The full phase plan with exit criteria is in [roadmap.md](docs/roadmap.md).

---

## Engineering rules (non-negotiable)

**Never:** hard-code secrets, providers, or AWS resources · expose chain-of-thought · trust client-side authorization · commit secrets · add microservices or Kubernetes without justification · bypass migrations or tests · fabricate benchmarks · make unsupported privacy claims.

**Always:** typed interfaces · dependency injection · structured logging · OpenTelemetry · migrations · automated tests · Terraform · least-privilege IAM · Secrets Manager · provider abstraction · tenant isolation · rate limiting · retries with timeouts · record AI usage, latency, and model selection · document decisions as ADRs.

---

## Review

Reviewers: please leave comments against specific document sections. Open questions are collected at the end of each document under **Open questions**.

Approval means: architecture, domain model, database schema, API contract, and roadmap are accepted as the basis for Phase 1.

---

*Related internal work: [`local-models`](../local-models/README.md) (Ollama serving on DGX Spark) and [`local-chat`](../local-chat/README.md) (LangChain gateway + Next.js chat) are precursors; Janus generalizes both.*
