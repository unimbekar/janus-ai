# Implementation Roadmap

**Status:** Draft for review (Phase 0) · **Last updated:** 2026-08-13

Ten phases. Each is a reviewable increment with explicit exit criteria; none begins until the previous one meets them. Phase 0 (this design set) must be approved before Phase 1.

Every phase ships: objective · architectural decisions · files created/modified · implementation · tests · security review · performance review · documentation.

---

## Phase 0 — Design *(current)*

| | |
|---|---|
| **Objective** | Agreed architecture, domain model, schema, API contract, and roadmap |
| **Deliverables** | The eleven documents in `docs/`, repository structure, database schema, API contract, model registry schema |
| **Exit criteria** | Reviewers approve architecture, domain model, schema, and API contract; the six open questions in [architecture.md §14](./architecture.md#14-open-questions) are answered; Sarvam assumptions verified against provider documentation |
| **Gate** | **No application code until this passes.** |

---

## Phase 1 — Foundation

| | |
|---|---|
| **Objective** | A deployable skeleton with the abstractions that matter already in place |
| **Scope** | Monorepo and tooling · Docker Compose · Next.js shell · FastAPI `janus-api` and `janus-gateway` · Aurora-compatible Postgres schema (core + registry) with Alembic · Redis · authentication (email/password, sessions, API keys) · organizations and membership · `ModelBackend` interface with **`MockBackend` and `OllamaBackend`** · registry-as-code loader · OpenTelemetry wiring · structured logging · CI |
| **Key decisions** | Provider abstraction exists from the first commit; runtime is a library inside `janus-api` ([ADR 0004](./adr/0004-ai-runtime-boundary.md)) |
| **Tests** | Unit, auth integration, RLS cross-tenant tests, conformance suite green on mock + Ollama |
| **Security review** | Password hashing, session and key handling, RLS enforcement, no secrets in repo |
| **Performance review** | Gateway overhead baseline measured against the mock backend |
| **Exit criteria** | `make up` gives a working local stack; a request flows web → api → gateway → Ollama; cross-tenant access tests pass; CI enforces boundary rules |

---

## Phase 2 — Chat

| | |
|---|---|
| **Objective** | A chat product people can use daily |
| **Scope** | Conversations and messages · SSE streaming end to end · **Sarvam adapter** (first cloud provider) · model selector · conversation history · cancellation and regeneration · Markdown, code blocks, tables · dark/light themes · keyboard shortcuts · attachments (upload + storage, parsing deferred) |
| **Key decisions** | Web uses `/v1/conversations/{id}/messages`, not raw chat completions; model attribution shown on every assistant message |
| **Tests** | Streaming, cancellation mid-stream, Indic script round-trip, message ordering under concurrency |
| **Security review** | XSS in rendered Markdown, upload validation and scanning, classification defaults |
| **Performance review** | TTFT p95 through the full path; streaming overhead per chunk |
| **Exit criteria** | Streaming chat with Sarvam and Ollama, persisted history, correct model attribution, no provider SDK imports outside the gateway |

---

## Phase 3 — Model Gateway

| | |
|---|---|
| **Objective** | The gateway becomes a real product surface, not an internal detail |
| **Scope** | Model registry and admin CRUD · model catalog UI and model detail pages · adapters for **OpenAI, Anthropic, Gemini, Bedrock** · public OpenAI-compatible endpoints (`/v1/chat/completions`, `/v1/embeddings`, `/v1/models`, `/v1/providers`) · health tracking and state machine · deterministic router with filters and scoring · **Auto mode** · capability aliases · routing decision log · usage records and cost calculation · rate limiting |
| **Key decisions** | Constraints hard / preferences soft; decision log from day one ([model-routing.md](./model-routing.md)) |
| **Tests** | Adapter conformance across all cloud providers, property tests on constraint safety, golden routing fixtures |
| **Security review** | Credential isolation, egress payload minimization, policy-filtered `/v1/models` |
| **Performance review** | Router decision p95 < 10 ms; gateway overhead p95 < 25 ms |
| **Exit criteria** | An external OpenAI SDK client works against Janus; Auto mode picks sensibly; every request has an explainable decision record |

---

## Phase 4 — Janus-hosted and local models

| | |
|---|---|
| **Objective** | Models on infrastructure Janus controls, behind the same gateway |
| **Scope** | **vLLM and SGLang adapters** · llama.cpp and MLX adapters for local/edge · deployment registry with hardware metadata · warm-up controller and readiness gating · health probes from workers · cross-plane fallback · deployment admin UI · `private` execution mode |
| **Key decisions** | Same weights in two places = one model, two deployments |
| **Tests** | Warming-state routing exclusion, fallback across planes, deployment-qualified model references |
| **Security review** | Private-subnet endpoints, no internet egress from inference, endpoint values never exposed |
| **Performance review** | Cold-start behavior, warm-pool effectiveness, throughput per runtime |
| **Exit criteria** | A self-hosted model serves production-shaped traffic (staging GPU or DGX Spark); `private` mode verifiably keeps data off external providers |

---

## Phase 5 — Agents

| | |
|---|---|
| **Objective** | Agents as versioned, governed artifacts |
| **Scope** | Agent CRUD, versioning, publishing · LangGraph execution with Postgres checkpointing · tool registry (native, REST, function) · **MCP client** · per-step model policies · human-in-the-loop approval · conversation summary memory · agent builder UI · run timeline UI · `/v1/responses` |
| **Key decisions** | Graph nodes never name a provider; tool output is untrusted data |
| **Tests** | Resume from checkpoint, budget and step-limit halts, tool timeout handling, injection resistance |
| **Security review** | Tool allow-listing, side-effect classification, approval gates, credential handling, scratchpad exposure |
| **Performance review** | Steps per run, cost per run, per-step routing effectiveness |
| **Exit criteria** | A published agent completes a multi-step task with tools, correct per-step model selection, full run telemetry, and no chain-of-thought leakage |

---

## Phase 6 — RAG and knowledge

| | |
|---|---|
| **Objective** | Grounded answers with citations |
| **Scope** | Document upload and parsing (PDF, DOCX, HTML, text) · structure- and script-aware chunking · embeddings through the gateway · pgvector storage with HNSW · hybrid search · reranking · citation binding · knowledge base management UI · ingestion pipeline on workers |
| **Key decisions** | Embedding model pinned per knowledge base; mixed-version search refused |
| **Tests** | Ingestion idempotency, dedupe, citation accuracy, Indic-script chunking, retrieval latency |
| **Security review** | Document classification propagation, org-scoped vector search, pre-signed URL scoping |
| **Performance review** | Retrieval p95, ingestion throughput, index size growth |
| **Exit criteria** | Documents in, cited answers out, org-isolated retrieval verified, re-embedding path exercised |

---

## Phase 7 — AWS production

| | |
|---|---|
| **Objective** | Production deployment with real operational discipline |
| **Scope** | Terraform for network, edge, ALB, ECS services, Aurora, Redis, S3, SQS, Secrets Manager, ECR, observability · CI/CD with migration gates · dashboards, alerts, runbooks · SLOs and error budgets · backups and a rehearsed restore · WAF and CloudFront · load testing |
| **Key decisions** | ECS Fargate for core; no EKS yet ([ADR 0003](./adr/0003-hybrid-ecs-eks.md)) |
| **Tests** | Infrastructure plan review, failover drill, restore drill, load test to target concurrency |
| **Security review** | IAM least privilege, network exposure, secret rotation, image hardening, WAF rules |
| **Performance review** | SLO baselines established under load; streaming through CloudFront/ALB verified |
| **Exit criteria** | Staging and production deploy from CI; SLOs measured; disaster recovery rehearsed; zero console-made changes |

---

## Phase 8 — GPU infrastructure

| | |
|---|---|
| **Objective** | Janus operates its own GPU fleet economically |
| **Scope** | EKS cluster and IRSA · GPU node pools (realtime, large, batch) · vLLM and SGLang deployments · model controller reconciling the deployment registry · weights cache · scale-to-zero and warm pools · GPU autoscaling · DCGM metrics · per-deployment cost dashboards |
| **Key decisions** | Kubernetes only for GPU serving; hardware chosen by pool capability, never hard-coded |
| **Tests** | Autoscaling under load, cold-start budgets, node failure recovery, controller reconciliation |
| **Security review** | Pod isolation, network policies, no egress from inference pods, weights integrity via hash pinning |
| **Performance review** | Tokens/sec per GPU-hour, utilization, cost per million tokens versus provider pricing |
| **Exit criteria** | A large open-weight model serves production traffic on Janus GPUs at a defensible cost, with honest amortized cost reporting |

---

## Phase 9 — Enterprise

| | |
|---|---|
| **Objective** | Sellable to regulated organizations |
| **Scope** | Full RBAC · SSO (OIDC/SAML) and SCIM · teams · full policy engine with the constraint set in [security.md §7](./security.md#7-policy-engine) · policy simulation endpoint · data classification enforcement · **sovereign mode** · audit log UI and export · quotas and cost ceilings · usage dashboards · bring-your-own-key (candidate) · customer-managed KMS (candidate) · SOC 2 control readiness |
| **Key decisions** | Constraints resolve most-restrictive-wins; no silent policy relaxation anywhere |
| **Tests** | Property tests on policy resolution, sovereign-mode egress verification, audit completeness |
| **Security review** | External audit readiness assessment; penetration test |
| **Performance review** | Policy resolution overhead; audit write throughput |
| **Exit criteria** | An organization can be configured so that confidential data provably never leaves Janus infrastructure, with an audit trail proving it |

---

## Phase 10 — Advanced AI

| | |
|---|---|
| **Objective** | Differentiation through breadth and measured intelligence |
| **Scope** | Multimodal (vision input, documents as first-class) · voice (speech-to-text and text-to-speech behind the same gateway) · advanced agents (planning, multi-agent delegation, long-term memory) · agent marketplace foundations · evaluation harness at scale with Indic evaluation sets · evaluation-driven routing weight tuning · opt-in learned routing with A/B measurement · web search tool |
| **Key decisions** | Learned routing ships only if measured to help without weakening constraint safety |
| **Tests** | Modality conformance, voice latency, routing A/B integrity |
| **Security review** | Audio and image handling, memory privacy controls, marketplace permission model |
| **Performance review** | Voice round-trip latency; router quality versus the deterministic baseline |
| **Exit criteria** | Multimodal and voice available; routing improvements demonstrated with Janus-measured evidence |

---

## Dependencies

```mermaid
flowchart LR
  P0["0 Design"] --> P1["1 Foundation"]
  P1 --> P2["2 Chat"]
  P2 --> P3["3 Model Gateway"]
  P3 --> P4["4 Janus-hosted models"]
  P3 --> P5["5 Agents"]
  P5 --> P6["6 RAG"]
  P3 --> P7["7 AWS production"]
  P4 --> P8["8 GPU infrastructure"]
  P7 --> P8
  P7 --> P9["9 Enterprise"]
  P6 --> P9
  P8 --> P10["10 Advanced AI"]
  P9 --> P10
```

Phases 4 and 5 can proceed in parallel after Phase 3 given separate owners (ML Infrastructure and Applied AI). Phase 7 depends only on Phase 3 and can overlap with 4–6.

---

## Cross-phase requirements

Present in **every** phase, never deferred to a "hardening phase":

| Requirement | Applies from |
|-------------|-------------|
| Typed interfaces, dependency injection, structured logging, OpenTelemetry | Phase 1 |
| Migrations reviewed; forward-only; RLS on every tenant table | Phase 1 |
| Automated tests including cross-tenant isolation | Phase 1 |
| No secrets in code; Secrets Manager for all credentials | Phase 1 |
| Provider abstraction; no vendor branching outside adapters (CI-enforced) | Phase 1 |
| Usage, latency, and model selection recorded for every AI call | Phase 3 |
| Rate limiting, timeouts, retries, circuit breakers | Phase 3 |
| ADR for every architectural decision | Phase 0 |
| No fabricated benchmarks; no unsupported privacy claims | Phase 0 |

---

## Risks to the plan

| Risk | Mitigation |
|------|------------|
| Sarvam assumptions prove wrong | Verify in Phase 0; registry is data-driven so corrections are configuration |
| GPU availability or cost blocks Phase 8 | Provider APIs remain viable indefinitely; Phase 8 is value-driven, not structural |
| Scope creep in Phase 2 (chat is endlessly polishable) | Fixed exit criteria; polish is a recurring allocation, not a blocking phase |
| Agent complexity underestimated in Phase 5 | Start with a constrained loop; general planning deferred to Phase 10 |
| Enterprise requirements arrive early from a design partner | Policy engine designed in Phase 0, so pulling Phase 9 items forward is incremental, not a rewrite |
| Team bandwidth | Phases 4/5 and 7 parallelize; otherwise sequence strictly |

---

## Open questions

1. Target date or headcount assumptions per phase — should this roadmap carry estimates, or stay sequence-only until the team is sized?
2. Is there a design partner whose requirements should pull Phase 9 items into Phase 5–6?
3. Does Phase 2 need attachments at all, or should uploads wait for Phase 6 when parsing exists?
4. Do we need a public beta gate between Phase 6 and Phase 7?
