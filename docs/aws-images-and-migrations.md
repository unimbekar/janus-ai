# AWS deploy — images & migrations (visual guide)

**Companion to:** [aws-deploy.md](./aws-deploy.md) §§6–7 · **Last updated:** 2026-08-16

Terraform (§5) builds the **empty parking garage**. Steps **6** and **7** put the
**cars** (container images) in the garage and paint the **floor lines** (database
schema). Without both, the ALB and ECS services exist but the product does not work.

---

## Big picture

```mermaid
flowchart LR
  subgraph done["§5 Terraform apply — already done"]
    ECR[ECR repos<br/>empty]
    ECS[ECS services<br/>want :latest]
    AUR[(Aurora<br/>no Janus tables)]
    SM[Secrets Manager<br/>DB URLs + tokens]
  end

  subgraph six["§6 Images"]
    DEV[Your machine<br/>Docker build]
    DEV -->|push amd64| ECR
    ECR -->|pull| ECS
  end

  subgraph seven["§7 Migrations"]
    MIG[One-off task<br/>alembic upgrade head]
    SM -.->|owner URL| MIG
    MIG -->|DDL + roles| AUR
  end

  ECS -->|runtime uses| AUR
```

| Step | Fills | If you skip it |
|------|--------|----------------|
| **§6** | ECR + running task containers | Tasks crash-loop / stay unhealthy |
| **§7** | Aurora schema + `janus_app` role | API up but sign-in / chat / `/readyz` fail |

Order: **§6 then §7** (migrate is easiest once the `api` image exists in ECR).

---

## §6 — Build and push images

### Why this exists

```mermaid
flowchart TB
  TF["Terraform created:<br/>ECR repos + ECS task defs<br/>pointing at …/api:latest"]
  TF --> Q{Is :latest<br/>in ECR?}
  Q -->|No| FAIL[Tasks fail to start<br/>or pull nothing useful]
  Q -->|Yes| OK[Fargate runs your code]
```

ECS never builds from your laptop. It only **pulls** from ECR. You must **build → tag → push**, then tell ECS to roll new tasks.

### Flow (what each command does)

```mermaid
sequenceDiagram
  participant You as Your machine (DGX / laptop)
  participant ECR as Amazon ECR
  participant ECS as ECS Fargate

  You->>ECR: docker login (aws ecr get-login-password)
  loop gateway, api, web
    You->>You: docker build --platform linux/amd64
    You->>ECR: docker push …/janus-staging/&lt;svc&gt;:latest
  end
  You->>ECS: update-service --force-new-deployment
  ECS->>ECR: pull :latest
  ECS->>ECS: replace old tasks with new ones
```

### What gets built

```mermaid
flowchart LR
  subgraph repo["janus-ai repo"]
    GW["services/gateway/Dockerfile<br/>+ registry/"]
    API["services/api/Dockerfile"]
    WEB["apps/web/Dockerfile"]
  end

  subgraph ecr["ECR (example)"]
    R1["…/janus-staging/gateway:latest"]
    R2["…/janus-staging/api:latest"]
    R3["…/janus-staging/web:latest"]
  end

  subgraph ecs["ECS cluster janus-staging"]
    S1[janus-staging-gateway]
    S2[janus-staging-api]
    S3[janus-staging-web]
  end

  GW --> R1 --> S1
  API --> R2 --> S2
  WEB --> R3 --> S3
```

### ARM note (DGX Spark)

| Host CPU | Fargate CPU | Build flag |
|----------|-------------|------------|
| ARM64 (Spark) | **amd64** | `PLATFORM=linux/amd64` |
| x86_64 laptop | amd64 | same flag is fine |

If you push an ARM-only image, ECS may pull it and the task will crash immediately.

### After §6 — what “good” looks like

```text
describe-services → running ≈ desired for api, gateway, web
CloudWatch /ecs/janus-staging/api shows the app starting
ALB target groups may still be unhealthy until §7 if /readyz needs schema
```

Commands: [aws-deploy.md §6](./aws-deploy.md#6-build-and-push-images).

---

## §7 — Run database migrations

### Why this exists

```mermaid
flowchart TB
  subgraph before["After §5 only"]
    A1[(Aurora cluster)]
    A1 --- E1[Empty / no Janus schemas]
  end

  subgraph after["After §7"]
    A2[(Aurora writer)]
    A2 --- S1[core.* conversations, orgs, …]
    A2 --- S2[telemetry.*]
    A2 --- S3[agent.* / knowledge.*]
    A2 --- R1[Role janus_app + RLS]
    A2 --- V1[extension vector]
  end

  before -->|alembic upgrade head| after
```

Two different database users:

| Who | Secret / URL | Job |
|-----|----------------|-----|
| **Migration** | `JANUS_MIGRATION_DATABASE_URL` (owner) | `CREATE TABLE`, roles, extensions |
| **Running api / gateway** | `JANUS_DATABASE_URL` (`janus_app`) | Normal app traffic under RLS |

If the app tried to migrate itself on every replica start, two tasks could race on DDL. Janus keeps migrate as an explicit one-shot (same idea as the local Compose `migrate` service).

### Preferred path — one-off ECS task

Aurora is in **private** subnets. Your laptop usually cannot open TCP to it. A short Fargate task in the **same VPC / security group as `api`** can.

```mermaid
flowchart TB
  subgraph public["Public"]
    ALB[ALB]
    YouLaptop[Your laptop]
  end

  subgraph private["Private app subnets"]
    API[api service<br/>long-running]
    MIG[one-off task<br/>command: alembic upgrade head]
  end

  subgraph data["Private data subnets"]
    AUR[(Aurora writer)]
    SM[Secrets Manager]
  end

  YouLaptop -.->|usually no route| AUR
  YouLaptop -->|aws ecs run-task| MIG
  MIG --> SM
  MIG -->|owner URL| AUR
  API --> SM
  API -->|janus_app URL| AUR
  ALB --> API
```

```mermaid
sequenceDiagram
  participant You
  participant ECS
  participant SM as Secrets Manager
  participant DB as Aurora

  You->>ECS: run-task (api image, command overridden)
  ECS->>SM: resolve JANUS_MIGRATION_DATABASE_URL
  ECS->>DB: alembic upgrade head
  DB-->>ECS: schemas + janus_app ready
  ECS-->>You: task exits 0
  Note over You: Restart / wait for api /readyz to go green
```

Console equivalent: **ECS → Task definitions → api → Deploy → Run task**, same subnets/SG as the api service, container command override:

```json
["alembic", "upgrade", "head"]
```

### Alternative — bastion / VPN

```mermaid
flowchart LR
  You[Laptop] --> VPN[VPN or bastion]
  VPN --> AUR[(Aurora)]
  You -->|alembic upgrade head<br/>with owner URL| AUR
```

Only use this if you already have network path into the data subnets. Load secrets locally; **do not paste them into chat or git**.

### After §7 — what “good” looks like

```text
Migrate task stopped with exit code 0
curl http://$ALB/readyz  → ok / schema checks pass
Browser: register workspace + chat (mock model on staging)
```

Commands: [aws-deploy.md §7](./aws-deploy.md#7-run-database-migrations).

---

## End-to-end checklist (visual)

```mermaid
flowchart TD
  A[§5 terraform apply] --> B[§6 build + push 3 images]
  B --> C[§6 force-new-deployment]
  C --> D{Tasks running?}
  D -->|No| B
  D -->|Yes| E[§7 alembic one-off task]
  E --> F{Task exit 0?}
  F -->|No| G[CloudWatch migrate/api logs]
  F -->|Yes| H[§8 curl ALB /healthz /readyz]
  H --> I[Open ALB in browser]
```

---

## Related

- Full runbook: [aws-deploy.md](./aws-deploy.md)
- Architecture: [aws.md](./aws.md)
- Ops / logs: [runbooks/troubleshooting.md](./runbooks/troubleshooting.md)
