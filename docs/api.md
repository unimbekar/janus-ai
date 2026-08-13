# API Contract

**Status:** Draft for review (Phase 0) · **Last updated:** 2026-08-13

Two surfaces, deliberately separated:

| Surface | Base | Audience | Component |
|---------|------|----------|-----------|
| **Inference API** | `https://api.janus-intelligence.ai/v1` | OpenAI-compatible clients, SDKs, LangChain/LangGraph, customer apps | `janus-gateway` |
| **Platform API** | `https://api.janus-intelligence.ai/v1` (distinct paths) | Web app, admin tooling, automation | `janus-api` |

The inference surface stays OpenAI-compatible so existing tooling works unchanged. Janus-specific behavior lives under a namespaced `janus` object that OpenAI clients ignore.

Related: [model-gateway.md](./model-gateway.md) · [security.md](./security.md) · [model-routing.md](./model-routing.md)

---

## 1. Conventions

| Aspect | Rule |
|--------|------|
| Transport | HTTPS only, TLS 1.2+; HTTP/2 |
| Encoding | JSON request/response; SSE for streaming; multipart for uploads |
| Versioning | Path major version (`/v1`); additive changes only within a major version |
| Auth | `Authorization: Bearer <api_key>` (programmatic) or session cookie (first-party web) |
| Org context | `X-Janus-Organization: org_…` when a key or session spans multiple orgs |
| Request id | `X-Request-Id` accepted; always returned; equals `janus.request_id` |
| Idempotency | `Idempotency-Key` on POST |
| Pagination | Cursor: `?limit=&cursor=`; response `{ data, has_more, next_cursor }` |
| Times | RFC 3339 UTC |
| Errors | Typed envelope ([§9](#9-error-model)) |
| Rate limits | `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `Retry-After` on 429 |

---

## 2. Authentication

| Method | Use | Notes |
|--------|-----|-------|
| API key | Server-side integrations | `jsk_live_…` / `jsk_test_…`; Argon2id hash stored, plaintext shown once |
| Session cookie | First-party web | `HttpOnly`, `Secure`, `SameSite=Lax`, CSRF token on mutations |
| OIDC / SSO | Enterprise sign-in (Phase 9) | SAML/OIDC → session |
| Service JWT | Internal service-to-service | Short-lived, audience-scoped, never accepted from the internet |

```http
POST /v1/chat/completions
Authorization: Bearer jsk_live_…
X-Janus-Organization: org_01JB…
Idempotency-Key: 8f1c…
Content-Type: application/json
```

Keys carry scopes and an optional `mode_ceiling`; a key restricted to `private` cannot cause inference on external providers regardless of request content.

---

## 3. Inference API

### 3.1 `POST /v1/chat/completions`

OpenAI-compatible request with optional Janus extensions.

```json
{
  "model": "auto",
  "messages": [
    { "role": "system", "content": "You are a careful contracts analyst." },
    { "role": "user", "content": "Summarize this master services agreement and flag unusual indemnity terms." }
  ],
  "stream": true,
  "temperature": 0.3,
  "max_tokens": 2048,
  "tools": [
    { "type": "function",
      "function": { "name": "lookup_statute",
                    "parameters": { "type": "object", "properties": { "code": { "type": "string" } } } } }
  ],
  "janus": {
    "mode": "private",
    "classification": "CONFIDENTIAL",
    "requirements": { "capabilities": ["reasoning", "long_context", "documents"], "languages": ["en"] },
    "constraints": { "max_cost_usd": 0.05, "max_latency_ms": 20000, "regions": ["us-east-1"] },
    "routing": { "explain": true },
    "conversation_id": "cnv_01JB…",
    "agent_id": "agt_01JB…"
  }
}
```

Non-streaming response:

```json
{
  "id": "chatcmpl_01JB…",
  "object": "chat.completion",
  "created": 1786800000,
  "model": "janus/llama-70b",
  "choices": [
    { "index": 0,
      "message": { "role": "assistant", "content": "…" },
      "finish_reason": "stop" }
  ],
  "usage": { "prompt_tokens": 5120, "completion_tokens": 842, "total_tokens": 5962 },
  "janus": {
    "request_id": "rq_01JB…",
    "model": "janus/llama-70b",
    "deployment": "janus-gpu-use1",
    "provider": "janus",
    "privacy": "private",
    "region": "us-east-1",
    "mode": "private",
    "fallback_used": false,
    "capability_downgraded": [],
    "routing_explanation": "Selected for long-context document reasoning under your organization's private-only policy.",
    "cost_usd": 0.0123,
    "ttft_ms": 640,
    "total_ms": 8210
  }
}
```

`model` in the response is the **resolved** model, so a client that sent `auto` learns what served it. Never returned: chain-of-thought, internal endpoints, candidate scores, other tenants' identifiers.

### 3.2 `POST /v1/embeddings`

```json
{ "model": "janus/embed-multilingual", "input": ["…", "…"], "janus": { "mode": "private" } }
```

Same policy, routing, and metering path as chat.

### 3.3 `GET /v1/models`

Policy-filtered for the caller — a key restricted to private deployments sees only models with an eligible private deployment.

```json
{
  "object": "list",
  "data": [
    {
      "id": "sarvam-105b",
      "object": "model",
      "owned_by": "sarvam",
      "janus": {
        "display_name": "Sarvam 105B",
        "tier": "recommended",
        "type": "chat",
        "context_window": 128000,
        "max_output_tokens": 8192,
        "capabilities": ["reasoning","agentic","tool_calling","long_context","multilingual","indic","streaming"],
        "languages": ["en","hi","te","ta"],
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "deployments": [
          { "key": "sarvam-cloud", "type": "provider_cloud", "privacy": "provider",
            "region": "us-east-1", "availability": "ready" },
          { "key": "janus-gpu-use1", "type": "janus_gpu", "privacy": "private",
            "region": "us-east-1", "availability": "ready" }
        ],
        "cost_class": "medium",
        "latency_class": "medium",
        "license": { "name": "…", "attribution_text": "…" }
      }
    }
  ]
}
```

Deployment `endpoint` values are never present. Benchmark figures appear only when measured by the Janus harness, with the eval run and date attached.

### 3.4 `GET /v1/models/{id}`

Full model detail: provider, deployments, context, capabilities, languages, observed latency class, availability, privacy, cost class, license and required attribution, and measured capability scores where they exist.

### 3.5 `GET /v1/providers`

```json
{ "data": [ { "id": "sarvam", "display_name": "Sarvam", "kind": "cloud_api",
              "status": "operational", "model_count": 2 } ] }
```

### 3.6 `GET /v1/deployments` — operator scope

Requires platform-operator scope. Adds backend, hardware, replicas, health metrics, and cost basis. Organization admins see a redacted view of only deployments serving their traffic.

### 3.7 `POST /v1/responses` — Janus-native (Phase 5)

Superset for agentic use: server-managed conversation state, tool-execution loop, retrieval, and typed step events. Chat completions remains the compatibility surface; `/v1/responses` is where Janus-specific capability lands.

### 3.8 Deferred (Phase 10)

`POST /v1/audio/transcriptions` · `POST /v1/audio/speech` · `POST /v1/images/generations` — designed for now, not implemented.

---

## 4. Platform API

### 4.1 Conversations and messages

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/v1/conversations` | List (cursor paginated) |
| `POST` | `/v1/conversations` | Create |
| `GET` | `/v1/conversations/{id}` | Detail with messages page |
| `PATCH` | `/v1/conversations/{id}` | Title, pinned model, mode |
| `DELETE` | `/v1/conversations/{id}` | Soft delete |
| `POST` | `/v1/conversations/{id}/messages` | Send message; SSE stream of assistant reply |
| `POST` | `/v1/conversations/{id}/messages/{msg}/regenerate` | New sibling message |
| `POST` | `/v1/conversations/{id}/cancel` | Cancel in-flight generation |

`POST /messages` persists the user message, runs the chat graph, streams the reply, and records model attribution and citations. It is the web app's primary endpoint; `/v1/chat/completions` is the stateless one.

### 4.2 Agents

| Method | Path | Purpose |
|--------|------|---------|
| `GET` / `POST` | `/v1/agents` | List / create |
| `GET` / `PATCH` / `DELETE` | `/v1/agents/{id}` | Detail / update draft / archive |
| `POST` | `/v1/agents/{id}/publish` | Freeze an immutable version |
| `GET` | `/v1/agents/{id}/versions` | Version history |
| `POST` | `/v1/agents/{id}/runs` | Start a run (sync stream or async) |
| `GET` | `/v1/agents/{id}/runs/{run}` | Run status, steps, usage |
| `POST` | `/v1/runs/{run}/approve` | Approve a pending tool call |
| `POST` | `/v1/runs/{run}/input` | Provide requested clarification |
| `POST` | `/v1/runs/{run}/cancel` | Cancel |

### 4.3 Tools and MCP

| Method | Path | Purpose |
|--------|------|---------|
| `GET` / `POST` | `/v1/tools` | List / register (native, REST, function, MCP) |
| `GET` / `PATCH` / `DELETE` | `/v1/tools/{id}` | Manage |
| `GET` / `POST` | `/v1/mcp-servers` | List / register |
| `POST` | `/v1/mcp-servers/{id}/test` | Connectivity and tool discovery |

### 4.4 Knowledge

| Method | Path | Purpose |
|--------|------|---------|
| `GET` / `POST` | `/v1/knowledge-bases` | List / create (embedding model fixed at creation) |
| `GET` / `DELETE` | `/v1/knowledge-bases/{id}` | Detail / delete |
| `POST` | `/v1/knowledge-bases/{id}/documents` | Upload (multipart) → async ingestion |
| `GET` | `/v1/knowledge-bases/{id}/documents` | List with ingestion status |
| `DELETE` | `/v1/documents/{id}` | Delete document and chunks |
| `POST` | `/v1/knowledge-bases/{id}/search` | Debug retrieval (returns chunks + scores) |

### 4.5 Organization, policy, usage

| Method | Path | Purpose |
|--------|------|---------|
| `GET` / `PATCH` | `/v1/organization` | Settings, default mode, residency |
| `GET` / `POST` / `DELETE` | `/v1/organization/members` | Membership and roles |
| `GET` / `POST` | `/v1/teams` | Teams |
| `GET` / `POST` / `DELETE` | `/v1/api-keys` | Keys (plaintext returned once, on create) |
| `GET` / `POST` | `/v1/policies` | Read / create a policy version |
| `POST` | `/v1/policies/simulate` | Dry-run: which models would be eligible for a hypothetical request |
| `GET` | `/v1/usage` | Aggregated usage; group by model, user, agent, day |
| `GET` | `/v1/usage/records` | Raw records (drill-down, paginated) |
| `GET` | `/v1/audit-events` | Audit log |

`POST /v1/policies/simulate` exists so administrators can verify a restriction *before* it silently blocks production traffic.

### 4.6 Health

| Path | Purpose |
|------|---------|
| `GET /healthz` | Liveness (no dependencies) |
| `GET /readyz` | Readiness (database reachable **and** at the expected schema version; registry loaded with at least one routable deployment) |
| `GET /v1/status` | Public component and provider status |

`/readyz` returns `503` when the instance cannot serve, so a load balancer acts on the status code and an operator reads the body. Two distinctions matter:

- A reachable database at an **unexpected schema version** is not ready. A deploy that outran its migration would otherwise report healthy while every query failed on a missing column.
- A **gateway** that is down does *not* make the control plane unready. Sign-in, organization management, and billing views still work without inference, and pulling the whole service would turn a partial outage into a total one.

---

## 5. Streaming

SSE with typed events. Chat completions streams OpenAI-shaped `data:` chunks plus Janus events at the boundaries.

```text
event: janus.routing
data: {"request_id":"rq_…","model":"janus/llama-70b","deployment":"janus-gpu-use1","fallback_used":false}

data: {"id":"chatcmpl_…","object":"chat.completion.chunk","choices":[{"delta":{"content":"The "}}]}

data: {"id":"chatcmpl_…","object":"chat.completion.chunk","choices":[{"delta":{"content":"agreement"}}]}

event: janus.usage
data: {"input_tokens":5120,"output_tokens":842,"cost_usd":0.0123,"ttft_ms":640}

data: [DONE]
```

Agent runs stream richer events: `janus.step.started`, `janus.tool.called`, `janus.tool.result`, `janus.retrieval`, `janus.approval_required`, `janus.step.completed`, `janus.error`.

| Rule | Detail |
|------|--------|
| Routing event first | Clients can show model attribution before tokens arrive |
| Cancellation | Client disconnect aborts upstream; partial usage still recorded |
| Mid-stream errors | `event: janus.error` with partial content preserved; the HTTP status is already 200 |
| Keep-alive | Comment heartbeats to survive proxy idle timeouts |
| No buffering | CloudFront/ALB configured for streaming passthrough |

---

## 6. Idempotency and retries

| Rule | Detail |
|------|--------|
| Scope | POST endpoints accept `Idempotency-Key`; keys are org-scoped and retained 24 hours |
| Replay | Same key + same body → cached response. Same key + different body → `idempotency_key_reuse` (409) |
| In-flight | Concurrent duplicate → `409 request_in_progress` |
| Client retries | Only on 429, 5xx, and network errors; exponential backoff with jitter, honoring `Retry-After` |
| Streaming | A key protects the *initiation*; a client that already received tokens must not blindly retry |

---

## 7. Error model

```json
{
  "error": {
    "type": "policy_violation",
    "code": "no_eligible_model",
    "message": "No model satisfies this request under the organization's private-only policy.",
    "param": null,
    "request_id": "rq_01JB…",
    "details": {
      "constraint": "privacy",
      "requested_mode": "private",
      "candidates_excluded": 7,
      "hint": "Enable a Janus-hosted long-context deployment, or relax the policy."
    },
    "retryable": false
  }
}
```

| HTTP | Type | Representative codes |
|------|------|----------------------|
| 400 | `invalid_request` | `missing_field`, `unsupported_parameter`, `context_length_exceeded` |
| 401 | `authentication` | `invalid_api_key`, `expired_session`, `revoked_key` |
| 403 | `authorization` | `insufficient_scope`, `not_a_member`, `resource_forbidden` |
| 403 | `policy_violation` | `no_eligible_model`, `mode_not_permitted`, `region_not_permitted`, `classification_forbidden` |
| 404 | `not_found` | `model_not_found`, `conversation_not_found` |
| 409 | `conflict` | `idempotency_key_reuse`, `request_in_progress`, `version_conflict` |
| 413 | `invalid_request` | `payload_too_large`, `file_too_large` |
| 422 | `unprocessable` | `tool_schema_invalid`, `embedding_model_mismatch` |
| 429 | `rate_limit` | `org_rate_limit`, `provider_rate_limited`, `quota_exceeded` |
| 499 | `cancelled` | `client_cancelled` |
| 500 | `internal` | `internal_error` |
| 502 | `provider_error` | `provider_auth_failed`, `provider_bad_response` |
| 503 | `unavailable` | `all_candidates_failed`, `model_warming`, `capacity_exceeded` |
| 504 | `timeout` | `provider_timeout`, `deadline_exceeded` |

Rules: codes are stable and machine-readable; messages are human-readable and safe; `details` never leaks internal endpoints, other tenants' data, provider keys, or chain-of-thought. A `policy_violation` explains the **constraint class** so an administrator can act, without revealing the full fleet.

---

## 8. Rate limits and quotas

| Layer | Enforcement |
|-------|-------------|
| Per API key | Requests/min and tokens/min from key configuration |
| Per organization | Plan-level ceilings across keys and users |
| Per user | Fair-use within an organization |
| Per deployment | Bulkhead concurrency (server-side protection, not a customer quota) |
| Provider | Respect upstream limits; surface as `provider_rate_limited` with a retry hint |

Redis-backed sliding windows. Cost quotas (`max_cost_usd` per period) are checked pre-flight against an estimate and reconciled post-flight against actuals.

---

## 9. Compatibility guarantees

**Stable within `/v1`:** existing field semantics, error `code` values, SSE event names, endpoint paths.

**May change:** new optional request fields, new response fields (including inside `janus`), new error codes for new conditions, new models and deployments.

**Never:** breaking a documented field's meaning without a new major version. `janus.*` fields are additive and safe for OpenAI-compatible clients to ignore.

An OpenAPI 3.1 document is generated from Pydantic models and published as the machine-readable contract in Phase 3; the SDKs are generated from it.

---

## 10. Open questions

1. Should the web app use `/v1/conversations/{id}/messages` exclusively (recommended), or also call `/v1/chat/completions` directly for stateless flows?
2. Do we return `janus.routing_explanation` by default, or only when `routing.explain` is true? (Recommendation: a short form always, detail on request.)
3. Are webhooks needed in Phase 5 for long-running agent runs, or is polling plus SSE sufficient?
4. Should organization admins see per-user usage by default, or is that a privacy setting?
5. Do we support OpenAI's `/v1/completions` legacy endpoint for compatibility, or chat-only?
