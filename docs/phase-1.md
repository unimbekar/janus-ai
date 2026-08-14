# Phase 1 — As Built

**Status:** complete · **Last updated:** 2026-08-13

[architecture.md](./architecture.md) describes where Janus is going. This document
describes what actually runs today, so the two can be read side by side and the gap
is always visible.

One sentence: a message travels browser → control plane → gateway → model and comes
back labeled with the model that produced it.

---

## 1. What runs

Four services, plus a one-shot migration job that must finish before the API starts.
The **only** path to a model is through the gateway
([ADR 0001](./adr/0001-gateway-sole-inference-path.md)), and that is enforced
mechanically — import-linter contracts fail the build if the control plane imports a
provider SDK or the gateway package.

```mermaid
flowchart TB
    Browser["Browser<br/>one origin, session cookie"]

    subgraph web["web · Next.js 16 · :3000"]
        Shell["chat + sign-in"] --> Proxy["/api/* proxy<br/>route handler"]
    end

    subgraph api["api · FastAPI · :8080 — control plane"]
        Auth["auth<br/>sessions, API keys"]
        Orgs["organizations<br/>members, keys"]
        Infer["inference<br/>passthrough"] --> GWClient["GatewayClient<br/>the only way out"]
    end

    DB[("PostgreSQL 16<br/>core + registry<br/>row-level security")]

    subgraph gw["gateway · FastAPI · :8081 — policy boundary"]
        Registry["Registry<br/>YAML, hot-reloaded"] --> Resolver["ModelResolver<br/>filter + rank"]
        Health["HealthTracker<br/>+ probe loop"] --> Resolver
        Resolver --> Executor["Executor<br/>attempt + fallback"]
    end

    subgraph backends["Backends · one ModelBackend interface"]
        Mock["mock<br/>deterministic"]
        Ollama["ollama<br/>local weights"]
        OAI["openai-compatible<br/>vLLM, SGLang, …"]
    end

    Browser --> Shell
    Proxy -->|"server side"| Auth
    Proxy --> Orgs
    Proxy --> Infer
    Auth --- DB
    Orgs --- DB
    GWClient -->|"service token +<br/>X-Janus-* policy context"| Resolver
    Executor --> Mock
    Executor --> Ollama
    Executor --> OAI

    style web fill:#eef2ff,stroke:#6d8cff
    style api fill:#f8fafc,stroke:#94a3b8
    style gw fill:#fffbeb,stroke:#fbbf24
    style backends fill:#f0fdf4,stroke:#4ade80
    classDef node fill:#ffffff,stroke:#64748b,color:#1e293b
    class Browser,Shell,Proxy,Auth,Orgs,Infer,GWClient,Registry,Health,Resolver,Executor,Mock,Ollama,OAI node
    style DB fill:#f0fdf4,stroke:#4ade80,color:#14532d
```

Ports shown are container ports; what gets published to the host is configurable
through `JANUS_WEB_PORT`, `JANUS_API_PORT`, and `JANUS_GATEWAY_PORT`.

Why the web server proxies rather than letting the browser call the API directly:
sessions stay same-origin, CORS never applies, and remote access needs one reachable
port instead of two — which is what makes the stack usable over an SSH tunnel or a
Tailscale address.

---

## 2. A streaming message, end to end

The ordering here is the product. Routing metadata is emitted **before** the first
token, so the UI can say which model is answering while the answer is still arriving.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant W as web
    participant A as api
    participant G as gateway
    participant M as Model backend

    B->>W: POST /api/v1/chat (cookie, stream: true)
    W->>A: same request, server side
    A->>A: authenticate cookie → Principal
    A->>A: resolve org policy → mode + classification
    Note over A: The control plane never picks a model.<br/>It supplies context and asks.
    A->>G: POST /v1/chat/completions<br/>+ service token, X-Janus-Mode, X-Janus-Classification

    G->>G: most restrictive of caller policy and request wins
    G->>G: resolve → ordered candidates
    G->>M: stream, first candidate

    alt first candidate fails before any token
        M--xG: retryable error
        Note over G: Nothing was sent yet,<br/>so switching models is still honest.
        G->>M: stream, next candidate
    end

    M-->>G: first chunk
    G-->>A: event: janus.routing
    A-->>W: bytes relayed unchanged
    W-->>B: model, privacy, explanation
    Note over B: UI labels the message<br/>before any text appears.

    loop content
        M-->>G: chunk
        G-->>B: data: {...} (through api + web)
    end

    G-->>B: event: janus.usage
    G-->>B: data: [DONE]
```

Once a single token has reached the client, the model is fixed. Silently switching
mid-answer would make the attribution a lie, so a later failure surfaces as a
terminal `janus.error` event instead.

---

## 3. How a model gets chosen

Constraints are hard and rejections are counted, so "no model available" always comes
with a reason rather than a shrug.

```mermaid
flowchart TD
    Req["model: 'auto' | slug | slug@deployment | janus/alias"]

    Req --> Pool{"candidate pool"}
    Pool -->|"auto"| All["every model × deployment"]
    Pool -->|"slug"| One["that model's deployments"]
    Pool -->|"slug@deployment"| Pin["exactly one — never substituted"]
    Pool -->|"janus/alias"| Alias["models with the capability"]

    All --> F1
    One --> F1
    Pin --> F1
    Alias --> F1

    subgraph Filter["first exclusion wins, and is recorded"]
        direction TB
        F1["disabled / inactive"] --> F2["execution mode<br/>cloud · private · sovereign · offline"]
        F2 --> F3["data classification<br/>CONFIDENTIAL+ never leaves"]
        F3 --> F4["region · provider"]
        F4 --> F5["capabilities · languages"]
        F5 --> F6["context window"]
        F6 --> F7["health: routable?"]
    end

    Filter --> Any{"anything left?"}
    Any -->|"no"| Err["PolicyViolationError<br/>+ dominant reason + hint"]
    Any -->|"yes"| Rank["rank: health, priority, tier, key"]
    Rank --> Try["attempt in order,<br/>up to 3 candidates"]
    Try --> Explain["generated explanation<br/>never model reasoning"]

    classDef gate fill:#fef3c7,stroke:#fbbf24,color:#451a03
    classDef bad fill:#fee2e2,stroke:#f87171,color:#450a0a
    classDef good fill:#f0fdf4,stroke:#4ade80,color:#14532d
    class Pool,Any gate
    class Err bad
    class Rank,Try,Explain good
```

Two rules in that diagram are load-bearing and deliberately not configurable:
a pinned deployment is never substituted, and `CONFIDENTIAL` or higher never reaches
an external provider whatever mode was requested.

---

## 4. Where tenancy is enforced

In the database, not in application code
([ADR 0005](./adr/0005-multi-tenancy-rls.md)). A repository that forgets a
`WHERE organization_id = …` returns nothing instead of another tenant's rows.

```mermaid
flowchart LR
    Req["request"] --> Principal["authenticate<br/>→ organization_id"]
    Principal --> Txn["open transaction"]
    Txn --> Scope["SET LOCAL janus.organization_id<br/>SET LOCAL janus.user_id"]
    Scope --> Query["query, with or without a WHERE clause"]
    Query --> RLS{"row-level security policy"}
    RLS -->|"organization_id matches"| Rows["rows"]
    RLS -->|"does not match"| Empty["no rows — not an error"]

    classDef gate fill:#fef3c7,stroke:#fbbf24,color:#451a03
    classDef good fill:#f0fdf4,stroke:#4ade80,color:#14532d
    class RLS gate
    class Rows,Empty good
```

Three details make that diagram hold up rather than merely look reassuring. The
services connect as `janus_app`, which has neither `BYPASSRLS` nor `SUPERUSER`.
Tenant tables are `FORCE ROW LEVEL SECURITY`, so the policies apply even to the table
owner. And `SET LOCAL` is scoped to the transaction, so a pooled connection cannot
carry one request's tenant context into the next.

Authentication itself is the one path that runs with **no** tenant scope, through a
narrow `SECURITY DEFINER` function: a key's organization is the *result* of
authentication, so it cannot also be an input to it.

---

## 5. Scope

```mermaid
flowchart TB
    subgraph P1["Phase 1 — built"]
        B1["gateway: routing, fallback, health"]
        B2["mock · ollama · openai-compatible"]
        B3["auth, orgs, members, API keys"]
        B4["RLS multi-tenancy"]
        B5["streaming chat UI with attribution"]
        B6["CI: lint, types, boundaries, 132 tests"]
    end

    subgraph P2["Phase 2 — next"]
        N1["persisted conversations"]
        N2["first frontier cloud adapter"]
        N3["cancellation, regeneration"]
        N4["attachments"]
    end

    subgraph Later["Phase 3+ — later"]
        L1["registry admin UI, public API"]
        L2["weighted scoring, rate limits"]
        L3["vLLM / SGLang on GPUs"]
        L4["agents, knowledge, workflows"]
    end

    P1 --> P2 --> Later

    classDef built fill:#dcfce7,stroke:#4ade80,color:#14532d
    classDef next fill:#eef2ff,stroke:#6d8cff,color:#1e293b
    classDef later fill:#ffffff,stroke:#cbd5e1,color:#475569
    class B1,B2,B3,B4,B5,B6 built
    class N1,N2,N3,N4 next
    class L1,L2,L3,L4 later
    style P1 fill:#f0fdf4,stroke:#4ade80
    style P2 fill:#f8fafc,stroke:#94a3b8
    style Later fill:#f8fafc,stroke:#cbd5e1
```

Deliberately **not** built yet, so nobody goes looking for it:

| Not here | Why |
|----------|-----|
| Persisted conversations | Phase 2. The inference route is a passthrough; nothing is stored. |
| Redis | Nothing caches, rate-limits, or holds shared state until Phase 3. Sessions live in Postgres. |
| Cloud provider adapters | The interface and its conformance suite exist; the first frontier adapter lands in Phase 2. |
| Weighted routing scores | Phase 1 ranks deterministically by health, priority, and tier. Scoring is Phase 3. |
| Durable usage records | Usage is computed and logged per request, but not yet billable state. |

---

## 6. Verifying it yourself

```bash
make stack-up                 # postgres, migrations, gateway, api, web
curl -s localhost:${JANUS_API_PORT:-8080}/readyz # {"status":"ready","checks":{...,"schema":"ok",...}}
make check                    # lint, types, boundaries, web checks, tests
```

The mock backend answers with no API key and no GPU, so the whole path above is
verifiable on a laptop. Its control tokens exercise the failure paths on purpose:

| Token in the last user message | Effect |
|---|---|
| `__fail__` | retryable failure on every deployment |
| `__fail_on__:<key>` | failure on one deployment — proves fallback |
| `__auth_fail__` | non-retryable credential failure |
| `__ratelimit__` · `__timeout__` | provider rate limit · request-budget timeout |
| `__slow__:<s>` · `__delay__:<s>` | delay before first token · between chunks |
| `__tokens__:<n>` | emit n chunks |

`__delay__` is the one to reach for when checking that nothing in the path buffers:
chunks should arrive spread out, not all at once at the end.
