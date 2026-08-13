# Database — Initial Schema

**Status:** Draft for review (Phase 0) · **Engine:** Aurora PostgreSQL 16 + pgvector · **Last updated:** 2026-08-13

Related: [architecture.md](./architecture.md) · [security.md](./security.md) · [model-registry.md](./model-registry.md) · [observability.md](./observability.md)

---

## 1. Conventions

| Convention | Rule |
|-----------|------|
| Primary keys | `TEXT` prefixed sortable ids (`org_`, `cnv_`, …), generated in application code (UUIDv7/ULID payload) |
| Tenancy | Every tenant-scoped table carries `organization_id` and is protected by row-level security |
| Timestamps | `TIMESTAMPTZ`, UTC, `created_at` / `updated_at` on mutable rows |
| Soft delete | `deleted_at` where user-recoverable; hard delete for compliance erasure |
| Enums | Postgres enums for stable domains; `TEXT` + check constraint where values evolve fast |
| JSON | `JSONB` for open-ended metadata; anything filtered or joined gets a real column |
| Money | `NUMERIC(18,8)` USD; never floating point |
| Migrations | Alembic, forward-only, reviewed ([§10](#10-migrations)) |
| Naming | `snake_case`, plural tables, singular columns |

Logical schemas group domains: `core`, `chat`, `agent`, `knowledge`, `registry`, `telemetry`.

---

## 2. Tenancy and identity (`core`)

```sql
CREATE TYPE execution_mode  AS ENUM ('auto','cloud','private','sovereign','offline');
CREATE TYPE classification  AS ENUM ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED');
CREATE TYPE org_role        AS ENUM ('owner','admin','member','viewer','billing');

CREATE TABLE core.organizations (
  id                    TEXT PRIMARY KEY,
  slug                  TEXT NOT NULL UNIQUE,
  name                  TEXT NOT NULL,
  plan                  TEXT NOT NULL DEFAULT 'free',
  default_mode          execution_mode NOT NULL DEFAULT 'auto',
  default_classification classification NOT NULL DEFAULT 'INTERNAL',
  data_residency        TEXT[] NOT NULL DEFAULT '{}',      -- ISO country codes; empty = unrestricted
  settings              JSONB NOT NULL DEFAULT '{}',
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at            TIMESTAMPTZ
);

CREATE TABLE core.users (
  id             TEXT PRIMARY KEY,
  email          TEXT NOT NULL UNIQUE,
  email_verified BOOLEAN NOT NULL DEFAULT false,
  name           TEXT,
  avatar_url     TEXT,
  password_hash  TEXT,                                     -- NULL when SSO-only
  mfa_secret_ref TEXT,                                     -- Secrets Manager reference, never the secret
  locale         TEXT NOT NULL DEFAULT 'en',
  status         TEXT NOT NULL DEFAULT 'active',
  last_login_at  TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at     TIMESTAMPTZ
);

CREATE TABLE core.organization_members (
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  user_id         TEXT NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
  role            org_role NOT NULL DEFAULT 'member',
  invited_by      TEXT REFERENCES core.users(id),
  joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, user_id)
);

CREATE TABLE core.teams (
  id              TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (organization_id, name)
);

CREATE TABLE core.team_members (
  team_id TEXT NOT NULL REFERENCES core.teams(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
  role    TEXT NOT NULL DEFAULT 'member',
  PRIMARY KEY (team_id, user_id)
);

CREATE TABLE core.api_keys (
  id              TEXT PRIMARY KEY,                        -- key_…
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  created_by      TEXT NOT NULL REFERENCES core.users(id),
  name            TEXT NOT NULL,
  prefix          TEXT NOT NULL,                           -- displayable head, e.g. jsk_live_ab12
  key_hash        TEXT NOT NULL,                           -- Argon2id of the full key; plaintext never stored
  scopes          TEXT[] NOT NULL DEFAULT '{}',
  mode_ceiling    execution_mode,                          -- optional narrower ceiling for this key
  rate_limit      JSONB NOT NULL DEFAULT '{}',
  last_used_at    TIMESTAMPTZ,
  expires_at      TIMESTAMPTZ,
  revoked_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON core.api_keys (organization_id) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX ON core.api_keys (key_hash);

CREATE TABLE core.sessions (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
  organization_id TEXT REFERENCES core.organizations(id) ON DELETE SET NULL,  -- active org context
  token_hash      TEXT NOT NULL UNIQUE,
  ip              INET,
  user_agent      TEXT,
  expires_at      TIMESTAMPTZ NOT NULL,
  revoked_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE core.audit_events (
  id              TEXT PRIMARY KEY,
  organization_id TEXT REFERENCES core.organizations(id) ON DELETE SET NULL,
  actor_type      TEXT NOT NULL,                           -- user | api_key | system
  actor_id        TEXT,
  action          TEXT NOT NULL,                           -- policy.updated, agent.published, key.revoked …
  resource_type   TEXT NOT NULL,
  resource_id     TEXT,
  ip              INET,
  metadata        JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON core.audit_events (organization_id, created_at DESC);
```

`core.audit_events` is append-only: no UPDATE or DELETE grants for application roles.

---

## 3. Policy (`core`)

```sql
CREATE TYPE policy_scope AS ENUM ('platform','organization','team','agent');

CREATE TABLE core.policies (
  id              TEXT PRIMARY KEY,                        -- pol_…
  scope           policy_scope NOT NULL,
  scope_id        TEXT,                                    -- NULL for platform scope
  organization_id TEXT REFERENCES core.organizations(id) ON DELETE CASCADE,
  version         INTEGER NOT NULL DEFAULT 1,
  mode            execution_mode,
  weight_profile  TEXT,
  weights         JSONB NOT NULL DEFAULT '{}',
  allow           JSONB NOT NULL DEFAULT '{}',             -- providers, models, regions, deployment_types
  deny            JSONB NOT NULL DEFAULT '{}',
  limits          JSONB NOT NULL DEFAULT '{}',             -- max cost, tokens, context
  classification_rules JSONB NOT NULL DEFAULT '{}',        -- classification → constraint overrides
  fallback        JSONB NOT NULL DEFAULT '{"enabled":true,"max_attempts":3}',
  is_active       BOOLEAN NOT NULL DEFAULT true,
  created_by      TEXT REFERENCES core.users(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (scope, scope_id, version)
);
```

Policies are **versioned and immutable in effect**: an edit inserts a new version, so a routing decision can cite the exact policy version that produced it.

---

## 4. Model registry (`registry`)

Platform-scoped, not tenant-scoped ([model-registry.md](./model-registry.md)).

```sql
CREATE TYPE model_type       AS ENUM ('chat','embedding','rerank','transcription','speech','image');
CREATE TYPE model_status     AS ENUM ('draft','evaluating','active','deprecated','disabled');
CREATE TYPE deployment_type  AS ENUM ('provider_cloud','janus_gpu','janus_cpu','local_dev','customer_vpc');
CREATE TYPE privacy_level    AS ENUM ('provider','private','local');
CREATE TYPE health_state     AS ENUM ('ready','warming','overloaded','degraded','offline','draining','provisioning');

CREATE TABLE registry.providers (
  id           TEXT PRIMARY KEY,
  slug         TEXT NOT NULL UNIQUE,                       -- sarvam, openai, anthropic, google, bedrock, janus…
  display_name TEXT NOT NULL,
  kind         TEXT NOT NULL,                              -- cloud_api | self_hosted | local
  status       TEXT NOT NULL DEFAULT 'active',
  metadata     JSONB NOT NULL DEFAULT '{}',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE registry.licenses (
  id                    TEXT PRIMARY KEY,                  -- lic_…
  name                  TEXT NOT NULL,
  spdx_id               TEXT,
  url                   TEXT,
  commercial_use        TEXT NOT NULL,                     -- permitted | permitted_with_conditions | prohibited
  attribution_required  BOOLEAN NOT NULL DEFAULT false,
  attribution_text      TEXT,
  redistribution_of_weights TEXT,
  acceptable_use_restrictions TEXT[] NOT NULL DEFAULT '{}',
  reviewed_by           TEXT REFERENCES core.users(id),
  reviewed_at           TIMESTAMPTZ,
  review_notes          TEXT
);

CREATE TABLE registry.models (
  id                TEXT PRIMARY KEY,                      -- mdl_…
  slug              TEXT NOT NULL UNIQUE,                  -- sarvam-105b | janus/llama-70b
  display_name      TEXT NOT NULL,
  family            TEXT,
  version           TEXT,
  provider_id       TEXT NOT NULL REFERENCES registry.providers(id),
  type              model_type NOT NULL DEFAULT 'chat',
  parameters        TEXT,
  architecture      TEXT,
  context_window    INTEGER NOT NULL,
  max_output_tokens INTEGER,
  input_modalities  TEXT[] NOT NULL DEFAULT '{text}',
  output_modalities TEXT[] NOT NULL DEFAULT '{text}',
  languages         TEXT[] NOT NULL DEFAULT '{}',
  capabilities      JSONB NOT NULL DEFAULT '{}',           -- declared capability flags
  cost_class        TEXT,
  latency_class     TEXT,
  tier              TEXT,                                  -- recommended | frontier | open_source | experimental
  status            model_status NOT NULL DEFAULT 'draft',
  license_id        TEXT REFERENCES registry.licenses(id),
  weights_source    TEXT,
  weights_sha256    TEXT,
  quantization      TEXT,
  metadata_verified BOOLEAN NOT NULL DEFAULT false,
  notes             TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON registry.models (status, type);
CREATE INDEX ON registry.models USING GIN (capabilities);
CREATE INDEX ON registry.models USING GIN (languages);

CREATE TABLE registry.model_deployments (
  id               TEXT PRIMARY KEY,                       -- dep_…
  key              TEXT NOT NULL,                          -- sarvam-cloud-in | janus-gpu-aps1
  model_id         TEXT NOT NULL REFERENCES registry.models(id) ON DELETE CASCADE,
  backend          TEXT NOT NULL,                          -- sarvam_api | vllm | sglang | ollama | mock…
  protocol         TEXT NOT NULL DEFAULT 'openai_compatible',
  endpoint_ref     TEXT,                                   -- config/secret reference, not a public value
  region           TEXT,
  deployment_type  deployment_type NOT NULL,
  privacy_level    privacy_level NOT NULL,
  data_residency   TEXT[] NOT NULL DEFAULT '{}',
  hardware         JSONB NOT NULL DEFAULT '{}',            -- gpu_type, gpu_count, node_pool
  replicas         JSONB NOT NULL DEFAULT '{}',
  max_context      INTEGER,
  max_concurrency  INTEGER,
  scale_to_zero    BOOLEAN NOT NULL DEFAULT false,
  warm_pool_floor  INTEGER NOT NULL DEFAULT 0,
  capability_overrides JSONB NOT NULL DEFAULT '{}',
  cost_basis       JSONB NOT NULL DEFAULT '{}',
  credentials_ref  TEXT,
  status           health_state NOT NULL DEFAULT 'provisioning',
  is_enabled       BOOLEAN NOT NULL DEFAULT true,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (model_id, key)
);
CREATE INDEX ON registry.model_deployments (status, is_enabled, privacy_level);

CREATE TABLE registry.model_aliases (
  alias        TEXT PRIMARY KEY,                           -- janus/fast, janus/reasoning
  description  TEXT,
  selection    JSONB NOT NULL DEFAULT '{}',                -- require capabilities, weight profile
  members      TEXT[] NOT NULL DEFAULT '{}',               -- model slugs (static membership)
  membership   TEXT NOT NULL DEFAULT 'dynamic'             -- dynamic | static
);

CREATE TABLE registry.model_prices (
  id                TEXT PRIMARY KEY,
  model_id          TEXT NOT NULL REFERENCES registry.models(id) ON DELETE CASCADE,
  deployment_id     TEXT REFERENCES registry.model_deployments(id) ON DELETE CASCADE,
  input_per_1m_usd  NUMERIC(18,8),
  output_per_1m_usd NUMERIC(18,8),
  cached_input_per_1m_usd NUMERIC(18,8),
  gpu_hour_usd      NUMERIC(18,8),                         -- for amortized janus_gpu basis
  currency          TEXT NOT NULL DEFAULT 'USD',
  effective_from    TIMESTAMPTZ NOT NULL,
  effective_to      TIMESTAMPTZ,
  source            TEXT                                    -- provider price page | contract | computed
);
CREATE INDEX ON registry.model_prices (model_id, effective_from DESC);

-- Health samples: high-volume, time-ordered; retention-managed
CREATE TABLE registry.health_samples (
  deployment_id   TEXT NOT NULL REFERENCES registry.model_deployments(id) ON DELETE CASCADE,
  observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  state           health_state NOT NULL,
  ttft_p95_ms     INTEGER,
  tokens_per_sec  NUMERIC(10,2),
  queue_depth     INTEGER,
  error_rate      NUMERIC(6,5),
  gpu_utilization NUMERIC(4,3),
  vram_utilization NUMERIC(4,3),
  PRIMARY KEY (deployment_id, observed_at)
);

-- Measured quality only; written exclusively by the evaluation harness
CREATE TABLE registry.model_capability_scores (
  model_id      TEXT NOT NULL REFERENCES registry.models(id) ON DELETE CASCADE,
  capability    TEXT NOT NULL,
  language      TEXT,
  score         NUMERIC(5,4) NOT NULL,
  eval_run_id   TEXT NOT NULL,
  measured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (model_id, capability, COALESCE(language,''), eval_run_id)
);
```

Live health for routing is served from Redis; `health_samples` is the durable series for trend analysis and capacity planning.

---

## 5. Chat (`chat`)

```sql
CREATE TYPE message_role AS ENUM ('system','user','assistant','tool');

CREATE TABLE chat.conversations (
  id              TEXT PRIMARY KEY,                        -- cnv_…
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  user_id         TEXT NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
  title           TEXT,
  agent_id        TEXT,                                    -- FK added with agent schema
  mode            execution_mode,
  classification  classification,
  pinned_model    TEXT,                                    -- model slug, or NULL for auto
  message_count   INTEGER NOT NULL DEFAULT 0,
  last_message_at TIMESTAMPTZ,
  metadata        JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ
);
CREATE INDEX ON chat.conversations (organization_id, user_id, last_message_at DESC)
  WHERE deleted_at IS NULL;

CREATE TABLE chat.messages (
  id               TEXT PRIMARY KEY,                       -- msg_…
  conversation_id  TEXT NOT NULL REFERENCES chat.conversations(id) ON DELETE CASCADE,
  organization_id  TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  role             message_role NOT NULL,
  sequence         INTEGER NOT NULL,
  content          JSONB NOT NULL,                         -- content parts: text, image_ref, file_ref
  status           TEXT NOT NULL DEFAULT 'complete',       -- streaming | complete | error | cancelled
  model_id         TEXT REFERENCES registry.models(id),
  deployment_id    TEXT REFERENCES registry.model_deployments(id),
  request_id       TEXT,                                   -- joins routing decision + usage
  input_tokens     INTEGER,
  output_tokens    INTEGER,
  cost_usd         NUMERIC(18,8),
  finish_reason    TEXT,
  error            JSONB,
  parent_message_id TEXT REFERENCES chat.messages(id),     -- regenerate / branch
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, sequence)
);
CREATE INDEX ON chat.messages (conversation_id, sequence);

CREATE TABLE chat.citations (
  id              TEXT PRIMARY KEY,
  message_id      TEXT NOT NULL REFERENCES chat.messages(id) ON DELETE CASCADE,
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  chunk_id        TEXT,
  document_id     TEXT,
  quote           TEXT,
  span            JSONB,
  score           NUMERIC(6,5)
);

CREATE TABLE chat.attachments (
  id              TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  message_id      TEXT REFERENCES chat.messages(id) ON DELETE CASCADE,
  uploaded_by     TEXT NOT NULL REFERENCES core.users(id),
  filename        TEXT NOT NULL,
  mime_type       TEXT NOT NULL,
  size_bytes      BIGINT NOT NULL,
  s3_key          TEXT NOT NULL,
  classification  classification NOT NULL DEFAULT 'INTERNAL',
  scan_status     TEXT NOT NULL DEFAULT 'pending',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Messages are immutable once `complete`; regeneration creates a new message linked by `parent_message_id`. Assistant messages record which deployment produced them, so model attribution survives fleet changes.

**No chain-of-thought is stored in `content`.** Internal reasoning artifacts live in `agent.agent_steps.scratchpad` with shorter retention ([security.md](./security.md#10-model-output-handling)).

---

## 6. Agents (`agent`)

```sql
CREATE TYPE agent_status AS ENUM ('draft','published','archived');
CREATE TYPE run_status   AS ENUM ('queued','running','awaiting_input','awaiting_approval',
                                  'completed','failed','cancelled','halted');

CREATE TABLE agent.agents (
  id              TEXT PRIMARY KEY,                        -- agt_…
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  slug            TEXT NOT NULL,
  name            TEXT NOT NULL,
  description     TEXT,
  status          agent_status NOT NULL DEFAULT 'draft',
  current_version INTEGER NOT NULL DEFAULT 0,
  visibility      TEXT NOT NULL DEFAULT 'organization',    -- private | team | organization | marketplace
  team_id         TEXT REFERENCES core.teams(id) ON DELETE SET NULL,
  created_by      TEXT NOT NULL REFERENCES core.users(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ,
  UNIQUE (organization_id, slug)
);

CREATE TABLE agent.agent_versions (
  id              TEXT PRIMARY KEY,
  agent_id        TEXT NOT NULL REFERENCES agent.agents(id) ON DELETE CASCADE,
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  version         INTEGER NOT NULL,
  instructions    TEXT NOT NULL,
  capabilities    JSONB NOT NULL DEFAULT '{}',
  model_policy    JSONB NOT NULL DEFAULT '{}',
  memory_config   JSONB NOT NULL DEFAULT '{}',
  knowledge_base_ids TEXT[] NOT NULL DEFAULT '{}',
  published_at    TIMESTAMPTZ,
  published_by    TEXT REFERENCES core.users(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (agent_id, version)
);

CREATE TABLE agent.mcp_servers (
  id              TEXT PRIMARY KEY,                        -- mcps_…
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  transport       TEXT NOT NULL,                           -- stdio | http | sse
  url             TEXT,
  auth_type       TEXT,
  credentials_ref TEXT,
  scopes          TEXT[] NOT NULL DEFAULT '{}',
  status          TEXT NOT NULL DEFAULT 'active',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent.tools (
  id              TEXT PRIMARY KEY,                        -- tol_…
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL,                           -- native | rest | function | mcp
  name            TEXT NOT NULL,
  description     TEXT NOT NULL,
  input_schema    JSONB NOT NULL,
  mcp_server_id   TEXT REFERENCES agent.mcp_servers(id) ON DELETE CASCADE,
  config          JSONB NOT NULL DEFAULT '{}',
  side_effects    TEXT NOT NULL DEFAULT 'read_only',       -- read_only | mutating | external_send
  data_classification_max classification NOT NULL DEFAULT 'INTERNAL',
  approval        TEXT NOT NULL DEFAULT 'auto',            -- auto | human_required
  timeout_ms      INTEGER NOT NULL DEFAULT 15000,
  rate_limit      JSONB NOT NULL DEFAULT '{}',
  credentials_ref TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (organization_id, name)
);

CREATE TABLE agent.tool_bindings (
  agent_version_id TEXT NOT NULL REFERENCES agent.agent_versions(id) ON DELETE CASCADE,
  tool_id          TEXT NOT NULL REFERENCES agent.tools(id) ON DELETE CASCADE,
  enabled          BOOLEAN NOT NULL DEFAULT true,
  approval         TEXT,                                   -- overrides tool default
  PRIMARY KEY (agent_version_id, tool_id)
);

CREATE TABLE agent.agent_runs (
  id               TEXT PRIMARY KEY,                       -- run_…
  organization_id  TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  agent_version_id TEXT NOT NULL REFERENCES agent.agent_versions(id),
  conversation_id  TEXT REFERENCES chat.conversations(id) ON DELETE SET NULL,
  triggered_by     TEXT REFERENCES core.users(id),
  api_key_id       TEXT REFERENCES core.api_keys(id),
  status           run_status NOT NULL DEFAULT 'queued',
  mode             execution_mode NOT NULL,
  resolved_policy  JSONB NOT NULL DEFAULT '{}',            -- snapshot for reproducibility
  step_count       INTEGER NOT NULL DEFAULT 0,
  input_tokens     INTEGER NOT NULL DEFAULT 0,
  output_tokens    INTEGER NOT NULL DEFAULT 0,
  cost_usd         NUMERIC(18,8) NOT NULL DEFAULT 0,
  halt_reason      TEXT,                                   -- budget_exceeded | max_steps_reached | …
  error            JSONB,
  started_at       TIMESTAMPTZ,
  finished_at      TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON agent.agent_runs (organization_id, created_at DESC);

CREATE TABLE agent.agent_steps (
  id             TEXT PRIMARY KEY,                         -- stp_…
  run_id         TEXT NOT NULL REFERENCES agent.agent_runs(id) ON DELETE CASCADE,
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  sequence       INTEGER NOT NULL,
  node           TEXT NOT NULL,                            -- plan | retrieve | tool | compose …
  model_id       TEXT REFERENCES registry.models(id),
  deployment_id  TEXT REFERENCES registry.model_deployments(id),
  request_id     TEXT,
  tool_id        TEXT REFERENCES agent.tools(id),
  tool_input     JSONB,
  tool_output    JSONB,
  scratchpad     JSONB,                                    -- internal only; shorter retention
  input_tokens   INTEGER,
  output_tokens  INTEGER,
  cost_usd       NUMERIC(18,8),
  latency_ms     INTEGER,
  status         TEXT NOT NULL,
  error          JSONB,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, sequence)
);

CREATE TABLE agent.checkpoints (
  run_id      TEXT NOT NULL REFERENCES agent.agent_runs(id) ON DELETE CASCADE,
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  step        INTEGER NOT NULL,
  state       JSONB NOT NULL,                              -- LangGraph checkpoint payload
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, step)
);
```

---

## 7. Knowledge and retrieval (`knowledge`)

```sql
CREATE TABLE knowledge.knowledge_bases (
  id              TEXT PRIMARY KEY,                        -- kb_…
  organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  description     TEXT,
  classification  classification NOT NULL DEFAULT 'INTERNAL',
  embedding_model_id TEXT NOT NULL REFERENCES registry.models(id),
  embedding_dimensions INTEGER NOT NULL,
  chunk_config    JSONB NOT NULL DEFAULT '{}',
  document_count  INTEGER NOT NULL DEFAULT 0,
  created_by      TEXT NOT NULL REFERENCES core.users(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (organization_id, name)
);

CREATE TABLE knowledge.documents (
  id                TEXT PRIMARY KEY,                      -- doc_…
  knowledge_base_id TEXT NOT NULL REFERENCES knowledge.knowledge_bases(id) ON DELETE CASCADE,
  organization_id   TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  title             TEXT,
  source_type       TEXT NOT NULL,                         -- upload | url | connector
  source_uri        TEXT,
  s3_key            TEXT,
  mime_type         TEXT,
  size_bytes        BIGINT,
  content_sha256    TEXT,
  language          TEXT,
  classification    classification NOT NULL DEFAULT 'INTERNAL',
  status            TEXT NOT NULL DEFAULT 'pending',       -- pending|parsing|embedding|ready|failed
  error             JSONB,
  chunk_count       INTEGER NOT NULL DEFAULT 0,
  created_by        TEXT REFERENCES core.users(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON knowledge.documents (knowledge_base_id, status);
CREATE UNIQUE INDEX ON knowledge.documents (knowledge_base_id, content_sha256)
  WHERE content_sha256 IS NOT NULL;                        -- dedupe within a base

CREATE TABLE knowledge.chunks (
  id                TEXT PRIMARY KEY,                      -- chk_…
  document_id       TEXT NOT NULL REFERENCES knowledge.documents(id) ON DELETE CASCADE,
  knowledge_base_id TEXT NOT NULL REFERENCES knowledge.knowledge_bases(id) ON DELETE CASCADE,
  organization_id   TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  sequence          INTEGER NOT NULL,
  content           TEXT NOT NULL,
  token_count       INTEGER,
  embedding         vector(1536),                          -- dimension per base; see note
  embedding_model_id TEXT NOT NULL REFERENCES registry.models(id),
  embedding_version TEXT NOT NULL,
  metadata          JSONB NOT NULL DEFAULT '{}',           -- page, section, heading path
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (document_id, sequence)
);

CREATE INDEX chunks_embedding_hnsw ON knowledge.chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX ON knowledge.chunks (knowledge_base_id);
CREATE INDEX chunks_content_fts ON knowledge.chunks
  USING GIN (to_tsvector('simple', content));               -- hybrid search; language-specific configs added per locale
```

Notes:

- Every chunk records its embedding model and version. Searching across mixed embedding versions is refused; changing a base's embedding model triggers a re-embedding job.
- `vector(1536)` is a placeholder. Because pgvector requires a fixed dimension per column, differing dimensions need either per-dimension partitions/tables or a normalization decision — see [§8](#8-vector-storage-decision-pending).
- `english` is the expected default config for the launch market; `simple` is used for non-Latin scripts where stemming configs are unavailable or harmful. Per-language configs are selected per knowledge base in Phase 6.

---

## 8. Vector storage — decision pending

| Option | Pros | Cons |
|--------|------|------|
| **pgvector in Aurora** (proposed for Phase 6) | One store, transactional consistency with metadata, RLS applies, no extra ops | Scaling limits at very large corpora; fixed dimension per column |
| OpenSearch / Aurora hybrid | Mature hybrid search and scale | Second datastore, sync complexity, separate tenancy enforcement |
| Dedicated vector DB | Best raw vector performance | New vendor, new tenancy model, cost |

Recommendation: pgvector through Phase 6, behind a `Retriever` interface with no leaked SQL, and a documented migration path. Revisit at either ~10M chunks per organization or p95 retrieval latency above 300 ms.

---

## 9. Telemetry and accounting (`telemetry`)

```sql
CREATE TABLE telemetry.routing_decisions (
  id                TEXT PRIMARY KEY,                      -- dec_…
  request_id        TEXT NOT NULL,
  organization_id   TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  user_id           TEXT REFERENCES core.users(id),
  api_key_id        TEXT REFERENCES core.api_keys(id),
  conversation_id   TEXT,
  agent_run_id      TEXT,
  requested_model   TEXT NOT NULL,                         -- auto | alias | slug | slug@deployment
  mode              execution_mode NOT NULL,
  classification    classification NOT NULL,
  requirements      JSONB NOT NULL DEFAULT '{}',
  policy_id         TEXT REFERENCES core.policies(id),
  policy_version    INTEGER,
  weight_profile    TEXT,
  candidates        JSONB NOT NULL,                        -- [{model, deployment, excluded_reason, scores}]
  selected_model_id TEXT REFERENCES registry.models(id),
  selected_deployment_id TEXT REFERENCES registry.model_deployments(id),
  routing_reason    TEXT,                                  -- safe explanation
  fallback_attempts JSONB NOT NULL DEFAULT '[]',
  fallback_used     BOOLEAN NOT NULL DEFAULT false,
  decision_ms       INTEGER,
  error_code        TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON telemetry.routing_decisions (organization_id, created_at DESC);
CREATE UNIQUE INDEX ON telemetry.routing_decisions (request_id);

CREATE TABLE telemetry.usage_records (
  id               TEXT PRIMARY KEY,
  request_id       TEXT NOT NULL,
  organization_id  TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  user_id          TEXT REFERENCES core.users(id),
  api_key_id       TEXT REFERENCES core.api_keys(id),
  conversation_id  TEXT,
  agent_run_id     TEXT,
  model_id         TEXT REFERENCES registry.models(id),
  deployment_id    TEXT REFERENCES registry.model_deployments(id),
  operation        TEXT NOT NULL,                          -- chat | embedding | rerank | transcription
  input_tokens     INTEGER NOT NULL DEFAULT 0,
  output_tokens    INTEGER NOT NULL DEFAULT 0,
  cached_tokens    INTEGER NOT NULL DEFAULT 0,
  ttft_ms          INTEGER,
  total_ms         INTEGER,
  tokens_per_sec   NUMERIC(10,2),
  cost_usd         NUMERIC(18,8) NOT NULL DEFAULT 0,
  cost_basis       TEXT,                                   -- provider_price | amortized_gpu_hour
  price_id         TEXT REFERENCES registry.model_prices(id),
  fallback_used    BOOLEAN NOT NULL DEFAULT false,
  capability_downgraded TEXT[] NOT NULL DEFAULT '{}',
  error_code       TEXT,
  occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON telemetry.usage_records (organization_id, occurred_at DESC);
CREATE INDEX ON telemetry.usage_records (deployment_id, occurred_at DESC);

CREATE TABLE telemetry.usage_rollups_daily (
  organization_id TEXT NOT NULL,
  day             DATE NOT NULL,
  model_id        TEXT,
  deployment_id   TEXT,
  operation       TEXT,
  requests        BIGINT NOT NULL DEFAULT 0,
  input_tokens    BIGINT NOT NULL DEFAULT 0,
  output_tokens   BIGINT NOT NULL DEFAULT 0,
  cost_usd        NUMERIC(18,8) NOT NULL DEFAULT 0,
  errors          BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (organization_id, day, COALESCE(model_id,''), COALESCE(deployment_id,''), COALESCE(operation,''))
);

CREATE TABLE telemetry.eval_runs (
  id            TEXT PRIMARY KEY,                          -- evl_…
  name          TEXT NOT NULL,
  dataset       TEXT NOT NULL,
  dataset_version TEXT,
  harness_version TEXT NOT NULL,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ,
  status        TEXT NOT NULL DEFAULT 'running',
  created_by    TEXT REFERENCES core.users(id)
);

CREATE TABLE telemetry.eval_results (
  eval_run_id   TEXT NOT NULL REFERENCES telemetry.eval_runs(id) ON DELETE CASCADE,
  deployment_id TEXT NOT NULL REFERENCES registry.model_deployments(id) ON DELETE CASCADE,
  metric        TEXT NOT NULL,                             -- quality | accuracy | latency | cost | safety…
  capability    TEXT,
  language      TEXT,
  value         NUMERIC(18,6) NOT NULL,
  sample_count  INTEGER NOT NULL,
  details       JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY (eval_run_id, deployment_id, metric, COALESCE(capability,''), COALESCE(language,''))
);
```

`usage_records` and `routing_decisions` are append-only and joined on `request_id`, which also appears on `chat.messages` and `agent.agent_steps` — one identifier links a user-visible message to its routing rationale and cost.

High-volume tables (`usage_records`, `routing_decisions`, `health_samples`) are partitioned monthly by time from the outset; retention and archive-to-S3 policies in [observability.md](./observability.md#8-retention).

---

## 10. Multi-tenancy enforcement

Defense in depth: application-level scoping **and** database-level row-level security, so a missing `WHERE organization_id = …` cannot leak data.

```sql
ALTER TABLE chat.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat.conversations FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON chat.conversations
  USING (organization_id = current_setting('janus.organization_id', true))
  WITH CHECK (organization_id = current_setting('janus.organization_id', true));
```

| Rule | Detail |
|------|--------|
| Applied to | Every table with `organization_id` |
| Session variable | `SET LOCAL janus.organization_id = '…'` per transaction, from the authenticated context |
| Application role | Non-superuser, no `BYPASSRLS` |
| Platform-scoped tables | `registry.*` readable by all tenants, writable only by the admin role |
| Migration role | Separate role; DDL only |
| Verification | Automated tests assert cross-tenant reads return zero rows for every tenant table; a new tenant table without an RLS policy fails CI |

Details and threat model in [security.md](./security.md#6-multi-tenancy).

---

## 11. Migrations

| Rule | Detail |
|------|--------|
| Tool | Alembic; one migration per pull request |
| Direction | Forward-only; `downgrade` may raise (rollback is a new forward migration) |
| Safety | No blocking table rewrites on hot tables; `CREATE INDEX CONCURRENTLY`; add-column-nullable then backfill then constrain |
| Backfills | Batched jobs, not inline in the migration |
| Enums | Additive only; removing a value requires a new type and a swap |
| Zero-downtime | Expand → migrate → contract across releases |
| Verification | Migrations run against a production-shaped snapshot in CI, with timing recorded |
| RLS | New tenant tables must include an RLS policy in the same migration |

---

## 12. Performance notes

| Query | Support |
|-------|---------|
| Conversation list per user | `(organization_id, user_id, last_message_at DESC)` partial index |
| Message thread load | `(conversation_id, sequence)` |
| Usage dashboard | Daily rollups; raw records only for drill-down |
| Router candidate fetch | Redis-cached registry snapshot; Postgres is not in the hot path |
| Vector search | HNSW per knowledge base, org-filtered before ANN where selective |
| Audit / decision search | Time-partitioned with `(organization_id, created_at DESC)` |

`updated_at` maintained by trigger. Connection pooling via PgBouncer-compatible transaction pooling — RLS session variables are set with `SET LOCAL` inside the transaction so they behave correctly under pooling.

---

## 13. Open questions

1. Do we partition `chat.messages` by time or organization at launch, or defer until volume justifies it?
2. Embedding dimension strategy — one table per dimension, per-base tables, or normalize all bases to a single dimension?
3. Retention defaults: conversations (indefinite?), scratchpad (30 days?), checkpoints (7 days?), health samples (90 days?)
4. Is per-organization schema or per-organization database ever required for enterprise customers, or is RLS sufficient contractually?
5. Should `usage_records` be the billing source of truth, or should a separate immutable ledger exist for invoicing?
