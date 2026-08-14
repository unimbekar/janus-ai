# Phase 2 — As Built

**Status:** complete · **Last updated:** 2026-08-14

[architecture.md](./architecture.md) describes where Janus is going. This document
describes what Phase 2 actually shipped — persisted chat, attachments storage,
and the control-plane path that turns streaming inference into durable history.

One sentence: a signed-in user sends a message to a **conversation**; the control
plane saves it, streams an answer from the gateway, and finalizes the assistant
turn with full model attribution — even when the model fails or the user cancels.

---

## 1. What runs

Phase 2 extends the Phase 1 stack. The gateway boundary is unchanged: still the
only path to a model. What is new is the **`chat` schema** in Postgres and the
**`/v1/conversations`** API that owns product state.

```mermaid
flowchart TB
    Browser["Browser<br/>session cookie"]

    subgraph web["web · Next.js · :3000"]
        Shell["chat UI<br/>model picker · attribution"]
        Proxy["/api/* proxy"]
    end

    subgraph api["api · FastAPI · :8080 — control plane"]
        Auth["auth · sessions"]
        Conv["/v1/conversations<br/>CRUD · messages · cancel · regenerate"]
        Attach["/v1/attachments<br/>upload · download"]
        Infer["/v1/chat<br/>stateless passthrough · still used by web"]
        Runner["ChatRunner<br/>persist + stream + finalize"]
        Cancel["CancellationRegistry<br/>per-instance"]
        Store["FilesystemObjectStore<br/>attachment bytes"]
    end

    DB[("PostgreSQL 16<br/>core + registry + chat<br/>RLS on all tenant tables")]

    subgraph gw["gateway · FastAPI · :8081"]
        Registry["Registry YAML"] --> Resolver["ModelResolver"]
        Health["HealthTracker"] --> Resolver
        Resolver --> Executor["Executor · fallback"]
    end

    subgraph backends["Backends"]
        Mock["mock"]
        Ollama["ollama"]
        OAI["openai_compatible"]
    end

    Browser --> Shell --> Proxy
    Proxy --> Auth
    Proxy --> Conv
    Proxy --> Attach
    Proxy --> Infer
    Conv --> Runner
    Runner -->|"service token + policy headers"| Executor
    Infer --> Executor
    Auth --- DB
    Conv --- DB
    Attach --- Store
    Attach --- DB
    Executor --> Mock
    Executor --> Ollama
    Executor --> OAI

    style web fill:#eef2ff,stroke:#6d8cff
    style api fill:#f8fafc,stroke:#94a3b8
    style gw fill:#fffbeb,stroke:#fbbf24
    style backends fill:#f0fdf4,stroke:#4ade80
    style DB fill:#f0fdf4,stroke:#4ade80,color:#14532d
```

**185 automated tests** pass (103 control plane · 82 gateway). Phase 1 had 132.

---

## 2. A conversation turn, end to end

The product API is **`POST /v1/conversations/{id}/messages`**, not raw chat
completions. The user's text is committed **before** the model is called, so a
provider failure never loses what they typed.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant W as web
    participant A as api
    participant DB as Postgres
    participant G as gateway
    participant M as Model backend

    B->>W: POST /api/v1/conversations/{cnv}/messages
    W->>A: same request (session cookie)
    A->>A: require signed-in user (not API key)
    A->>DB: INSERT user message (committed)
    A->>DB: INSERT assistant row status=streaming
    A->>G: POST /v1/chat/completions (history, stream)

    G->>G: policy · resolve · rank candidates
    G->>M: stream first candidate

    G-->>A: event: janus.routing
    A-->>W: relay unchanged
    W-->>B: model + privacy + explanation

    loop tokens
        M-->>G: chunk
        G-->>A: data: {...}
        A-->>W: relay
        W-->>B: render
    end

    G-->>A: event: janus.usage
    G-->>A: data: [DONE]
    A->>DB: UPDATE assistant row → complete<br/>attribution · tokens · explanation
    A-->>B: stream ends

    Note over A,DB: Disconnect or cancel mid-stream<br/>still finalizes partial text
```

**Regeneration** appends a new assistant message with `parent_message_id` pointing
at the old attempt; finalized messages are immutable (database trigger).

**Cancel** (`POST /v1/conversations/{id}/cancel`) signals in-flight generations on
**this API instance only** until Phase 3 adds Redis pub/sub.

---

## 3. Chat data model

```mermaid
erDiagram
    ORGANIZATION ||--o{ CONVERSATION : owns
    USER ||--o{ CONVERSATION : starts
    CONVERSATION ||--o{ MESSAGE : contains
    MESSAGE ||--o{ ATTACHMENT : "may reference"
    MESSAGE }o--o| MESSAGE : "parent (regenerate)"

    CONVERSATION {
        text id PK
        text organization_id FK
        text user_id FK
        text title
        text pinned_model
        execution_mode mode
        int message_count
        timestamptz last_message_at
    }

    MESSAGE {
        text id PK
        text conversation_id FK
        message_role role
        int sequence
        jsonb content
        text status
        text model_slug
        text deployment_key
        text provider
        text privacy
        bool fallback_used
        text routing_explanation
        int input_tokens
        int output_tokens
        text parent_message_id
    }

    ATTACHMENT {
        text id PK
        text organization_id FK
        text conversation_id FK
        text filename
        text mime_type
        int size_bytes
        text storage_key
        text scan_status
    }
```

Design choices (migration `0002_chat.py`):

| Choice | Rationale |
|--------|-----------|
| Attribution as slugs, not FKs to `registry.models` | Catalog is still YAML; FKs to empty registry tables would block writes |
| Immutable finalized messages | Trigger rejects updates once `status ≠ streaming` |
| Atomic sequence counter on conversation | Two tabs sending at once get distinct sequences |
| No `chat.citations` yet | Retrieval is Phase 6 |
| Soft-delete conversations | Transcripts are not hard-deleted from request paths |

---

## 4. Attachments (storage only)

```mermaid
flowchart LR
    Upload["POST …/attachments<br/>multipart upload"] --> Validate["validate_upload<br/>size · mime · extension"]
    Validate --> Key["storage_key<br/>org-scoped path"]
    Key --> FS["FilesystemObjectStore"]
    Validate --> Row["chat.attachments<br/>scan_status=pending"]
    Download["GET …/attachments/{id}"] --> Headers["Content-Disposition: attachment<br/>nosniff · CSP sandbox"]
    Headers --> FS

    style Validate fill:#fef3c7,stroke:#fbbf24
    style Headers fill:#fef3c7,stroke:#fbbf24
```

Nothing parses PDFs, extracts text, or sends bytes to a model — that is Phase 6.
`scan_status` stays **`pending`** so unscanned files are never assumed clean.

---

## 5. Scope

```mermaid
flowchart TB
    subgraph P2["Phase 2 — built"]
        B1["chat schema + RLS"]
        B2["conversations CRUD + pagination"]
        B3["streaming send · finalize · regenerate"]
        B4["in-process cancellation"]
        B5["attachments upload/download"]
        B6["ChatRunner · attribution persisted"]
        B7["103 new/updated API tests"]
    end

    subgraph P3["Phase 3 — next"]
        N1["weighted routing scores"]
        N2["routing_decisions + usage_records"]
        N3["Redis · rate limits · cross-instance cancel"]
        N4["frontier cloud adapters"]
        N5["public OpenAI-compatible API keys"]
        N6["web → conversations API"]
    end

    subgraph Later["Phase 4+"]
        L1["Janus GPU · vLLM/SGLang"]
        L2["agents · LangGraph"]
        L3["RAG · citations"]
    end

    P2 --> P3 --> Later

    classDef built fill:#dcfce7,stroke:#4ade80,color:#14532d
    classDef next fill:#eef2ff,stroke:#6d8cff,color:#1e293b
    classDef later fill:#ffffff,stroke:#cbd5e1,color:#475569
    class B1,B2,B3,B4,B5,B6,B7 built
    class N1,N2,N3,N4,N5,N6 next
    class L1,L2,L3 later
    style P2 fill:#f0fdf4,stroke:#4ade80
    style P3 fill:#f8fafc,stroke:#94a3b8
    style Later fill:#f8fafc,stroke:#cbd5e1
```

### Deliberately not built yet

| Not here | Why |
|----------|-----|
| Web wired to `/v1/conversations` | UI still calls stateless `/v1/chat`; history is API-ready, web catches up in Phase 3 |
| Frontier cloud adapters in registry | Interface exists; first provider entries + credentials land in Phase 3 |
| Redis | Rate limits, shared health, cross-tab cancel — Phase 3 |
| Durable routing/usage tables | Logged structurally; Postgres persistence — Phase 3 |
| Weighted scoring | Phase 1/2 rank by health · priority · tier; scoring — Phase 3 |
| Document parsing / RAG | Phase 6 |
| Thread sidebar in web | Phase 3 catalog UI |

---

## 6. Key files

| Area | Path |
|------|------|
| Migration | `services/api/migrations/versions/0002_chat.py` |
| Conversations API | `services/api/api_app/routers/conversations.py` |
| Chat streaming + DB | `services/api/api_app/chat_stream.py` |
| Domain logic | `services/api/api_app/conversations.py` |
| Attachments | `services/api/api_app/routers/attachments.py`, `storage.py` |
| Cancellation | `services/api/api_app/cancellation.py` |
| Tests | `services/api/tests/test_conversations.py`, `test_attachments.py`, `test_sse.py` |

---

## 7. Verifying it yourself

On this machine ports 3000/8080 are taken, so the stack publishes API **8090** and
web **3010** (see `.env`). The default in the examples is 8080.

```bash
make stack-up
make test-api          # needs Postgres
make test-gateway

API="localhost:${JANUS_API_PORT:-8080}"

# Sign in (or register) so curl has a session cookie:
curl -s -c cookies.txt -X POST "$API/v1/auth/register" \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"correct-horse-battery","name":"You","organization_name":"Acme"}'

curl -s -b cookies.txt -X POST "$API/v1/conversations" \
  -H 'Content-Type: application/json' -d '{}'
curl -s -N -b cookies.txt -X POST "$API/v1/conversations/cnv_…/messages" \
  -H 'Content-Type: application/json' -d '{"content":"Hello"}'
```

Reload the conversation — history and model attribution survive:

```bash
curl -s -b cookies.txt "$API/v1/conversations/cnv_…"
```
