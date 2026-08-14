# Troubleshooting — where to look

**Last updated:** 2026-08-14

When something fails, start with the **request id**, then the **service that owns that hop**, then **domain tables** (routing / usage / agent runs). Prompts and completions are never in logs by design.

---

## 1. Grab the request id

Every API/gateway response includes:

```http
X-Janus-Request-Id: rq_…
```

Error JSON also carries `request_id` when available. Chat SSE emits the same id on `janus.routing` / usage events.

Search logs and SQL with that `rq_…` value.

---

## 2. Local stack (Docker Compose)

From the repo root:

```bash
# Which containers are up?
docker compose --profile full ps

# Live logs (pick the failing hop)
docker compose --profile full logs -f api
docker compose --profile full logs -f gateway
docker compose --profile full logs -f web
docker compose --profile full logs -f migrate   # one-shot; check if migrations failed

# Filter by request id once you have it
docker compose --profile full logs api gateway 2>&1 | grep rq_YOUR_ID
```

| Symptom | Check first |
|---------|-------------|
| UI blank / “Loading…” | `web` logs; `curl localhost:$JANUS_WEB_PORT/healthz` |
| Sign-in / 5xx from `/api/*` | `api` logs; `curl localhost:$JANUS_API_PORT/readyz` |
| Chat streams then dies | `api` then `gateway`; look for provider/timeout/policy errors |
| “No eligible model” | `gateway` logs + mode (`private`/`sovereign`) vs catalog |
| Migration / schema errors | `migrate` container exit logs; `/readyz` → `checks.schema` |
| DB connection refused | `postgres` healthy? `JANUS_*_PORT` collisions in `.env` |

Health endpoints (no auth):

```bash
curl -s localhost:${JANUS_WEB_PORT:-3010}/healthz
curl -s localhost:${JANUS_API_PORT:-8090}/readyz
curl -s localhost:${JANUS_GATEWAY_PORT:-8091}/readyz
```

---

## 3. Domain data in Postgres (local or Aurora)

These are product records, not just ops logs — use them when the UI says “wrong model” or “agent failed”.

```sql
-- Why this model?
SELECT * FROM telemetry.routing_decisions
WHERE request_id = 'rq_…';

-- Tokens / cost / failure
SELECT * FROM telemetry.usage_records
WHERE request_id = 'rq_…';

-- Agent run
SELECT id, status, halt_reason, error, step_count
FROM agent.agent_runs WHERE id = 'run_…';

SELECT sequence, node, tool_name, model_slug, status, error
FROM agent.agent_steps WHERE run_id = 'run_…'
ORDER BY sequence;
```

Local:

```bash
docker compose exec -T postgres psql -U janus -d janus
```

---

## 4. AWS (after `terraform apply`)

| What | Where |
|------|--------|
| Service logs | CloudWatch Logs → `/ecs/janus-<env>/web`, `…/api`, `…/gateway` |
| Task crashes / OOM | ECS → cluster `janus-<env>` → service → Tasks / Events |
| 502 / unhealthy targets | EC2 → Load Balancers → target groups `…-web` / `…-api` → Health |
| Secrets / DB URL wrong | Secrets Manager → `janus-<env>/app` (do not paste into chat) |
| Schema not migrated | One-off migrate task / bastion: `alembic upgrade head`; API `/readyz` |
| Rate limits / Redis | ElastiCache metrics + gateway logs (`rate_limit`) |

Example:

```bash
export AWS_PROFILE=janus AWS_REGION=us-east-1
PREFIX=janus-staging   # name_prefix-environment

aws logs tail "/ecs/${PREFIX}/api" --follow
aws logs tail "/ecs/${PREFIX}/gateway" --follow --filter-pattern "rq_"
aws ecs describe-services --cluster "$PREFIX" \
  --services "${PREFIX}-api" "${PREFIX}-gateway" "${PREFIX}-web" \
  --query 'services[].{name:serviceName,running:runningCount,desired:desiredCount}'
```

Deploy runbook: [aws-deploy.md](../aws-deploy.md).

---

## 5. What you will *not* find in logs

By policy ([security.md](../security.md)):

- User prompts / model completions  
- Chain-of-thought / agent scratchpads  
- API keys, passwords, provider credentials  
- Internal model endpoints  

Use routing explanations, error codes, and DB telemetry instead of fishing for message bodies.

---

## 6. Quick decision tree

```text
UI problem only?          → web logs + browser network tab (/api proxy)
Auth / org / conversations? → api logs + core.* tables
Model / routing / stream? → gateway logs + telemetry.routing_decisions
Agent / knowledge?        → api logs + agent.* / knowledge.*
Infra / 5xx after deploy? → ECS events + CloudWatch + ALB target health
```
