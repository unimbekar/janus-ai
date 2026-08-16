# Troubleshooting — where to look

**Last updated:** 2026-08-16

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

```bash
./install.sh status

docker compose --profile full ps

# Live logs while chatting
docker compose --profile full logs -f api gateway

docker compose --profile full logs -f web
docker compose --profile full logs migrate   # one-shot; check exit if schema fails

docker compose --profile full logs api gateway 2>&1 | grep rq_YOUR_ID
```

| Symptom | Check first |
|---------|-------------|
| `Bind … port is already allocated` | `.env`: **`JANUS_API_PORT` ≠ `JANUS_GATEWAY_PORT`**; `ss -ltn` for collisions |
| UI blank / “Loading…” | `web` logs; `curl localhost:$JANUS_WEB_PORT/healthz` |
| Sign-in / 5xx from `/api/*` | `api` logs; `curl localhost:$JANUS_API_PORT/readyz` |
| Chat streams then dies | `api` then `gateway`; provider/timeout/policy errors |
| “No eligible model” | `gateway` logs; mode vs catalog; Ollama reachability (below) |
| Local model missing from Models tab | `config/local-models.yaml` + `registry/environments/local.yaml`; `./install.sh ensure-models`; Ollama must listen on `0.0.0.0` for Compose |
| Migration / schema errors | `migrate` exit logs; `/readyz` → `checks.schema` |
| DB connection refused | `postgres` healthy? port collisions in `.env` |

Health (no auth):

```bash
source .env
curl -s "localhost:${JANUS_WEB_PORT}/healthz"
curl -s "localhost:${JANUS_API_PORT}/readyz"
curl -s "localhost:${JANUS_GATEWAY_PORT}/readyz"
```

### Local Ollama

```bash
./install.sh ensure-models
curl -s http://127.0.0.1:11434/api/tags | python3 -m json.tool | head
# From the gateway container:
docker compose exec gateway python -c \
  "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags', timeout=5).status)"
```

Compose uses `JANUS_OLLAMA_COMPOSE_URL` (`host.docker.internal`). Host-mode uses `JANUS_OLLAMA_BASE_URL` (`127.0.0.1`). Loopback-only Ollama (`127.0.0.1:11434`) is unreachable from containers — `./install.sh start` rebinds to `0.0.0.0:11434` when needed.

---

## 3. Domain data in Postgres (local or Aurora)

```sql
SELECT * FROM telemetry.routing_decisions WHERE request_id = 'rq_…';
SELECT * FROM telemetry.usage_records WHERE request_id = 'rq_…';

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
| Service logs | CloudWatch `/ecs/janus-<env>/web`, `…/api`, `…/gateway` |
| Task crashes / OOM | ECS → cluster `janus-<env>` → Tasks / Events |
| 502 / unhealthy targets | ALB target groups `…-web` / `…-api` |
| Secrets / DB URL wrong | Secrets Manager `janus-<env>/…` (do not paste into chat) |
| Schema not migrated | One-off migrate task ([aws-deploy.md](../aws-deploy.md) §7); API `/readyz` |
| Empty / wrong catalog | `JANUS_ENVIRONMENT` vs `registry/environments/<env>.yaml` (staging ships mocks only) |
| Images never updated | Push to ECR then `aws ecs update-service … --force-new-deployment` |

```bash
export AWS_PROFILE=janus AWS_REGION=us-east-1
CLUSTER=janus-staging   # terraform output -raw ecs_cluster

aws logs tail "/ecs/${CLUSTER}/api" --follow
aws logs tail "/ecs/${CLUSTER}/gateway" --follow --filter-pattern "rq_"
aws ecs describe-services --cluster "$CLUSTER" \
  --services "${CLUSTER}-api" "${CLUSTER}-gateway" "${CLUSTER}-web" \
  --query 'services[].{name:serviceName,running:runningCount,desired:desiredCount}'
```

ALB: `/` → web; `/v1/*` + `/healthz` + `/readyz` → api. Gateway is **not** on the ALB.

Deploy runbook: [aws-deploy.md](../aws-deploy.md).

---

## 5. What you will *not* find in logs

By policy ([security.md](../security.md)):

- User prompts / model completions  
- Chain-of-thought / agent scratchpads  
- API keys, passwords, provider credentials  
- Internal model endpoints  

Use routing explanations, error codes, and DB telemetry instead.

---

## 6. Quick decision tree

```text
UI problem only?            → web logs + browser network (/api proxy)
Auth / org / conversations? → api logs + core.* tables
Model / routing / stream?   → gateway logs + telemetry.routing_decisions
Local model missing?        → ensure-models + Ollama bind + local.yaml
Agent / knowledge?          → api logs + agent.* / knowledge.*
Infra / 5xx after deploy?   → ECS events + CloudWatch + ALB target health
```
