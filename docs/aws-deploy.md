# Deploy Janus on AWS

**Status:** as-built for Phase 7 · **Last updated:** 2026-08-16

This is the runbook for applying [`infra/aws`](../infra/aws) to **your** AWS
account. Architecture context: [aws.md](./aws.md). Marketplace prep:
[marketplace.md](./marketplace.md). Local product path: [README.md](../README.md).

**Never paste access keys into chat, tickets, or git.** Configure the AWS CLI
(or temporary env vars) on the machine that runs Terraform.

---

## What you get after a successful deploy

| Piece | AWS resource |
|-------|----------------|
| UI + API | ALB → ECS Fargate (`web`, `api`); gateway is **internal** (service discovery) |
| Database | Aurora PostgreSQL 16 Serverless v2 + pgvector |
| Cache | ElastiCache Redis |
| Images | ECR (`gateway`, `api`, `web`) |
| Secrets | Secrets Manager (`JANUS_*` URLs and tokens) |
| Logs | CloudWatch `/ecs/janus-<environment>/…` |

Default `environment = "staging"` → cluster / prefix **`janus-staging`**.

**Not included on day one:** host Ollama / DGX local models, GPU EKS
(`enable_gpu_eks = false`), ACM HTTPS (optional), Marketplace listing.

Staging catalog uses [`registry/environments/staging.yaml`](../registry/environments/staging.yaml)
(mock models only). Cloud providers are opt-in after you add secrets.

---

## Quick path (recommended)

From the repo root on a machine with Docker:

```bash
# Tools + credentials + terraform.tfvars + optional state bootstrap / plan / apply
./setup.sh --aws
# or non-interactive apply (costs money):
./setup.sh --aws --apply --yes
```

Then finish **§6 Build and push images** and **§7 Migrations** below — Terraform
alone does not put application images into ECS or migrate Aurora.

Developer tools-only (DGX / existing CLI install):

```bash
./install.sh --tools-only   # Terraform, AWS CLI, gh, Node under ~/.local
export PATH="$HOME/.local/bin:$PATH"
```

---

## Checklist before you spend money

- [ ] AWS account ID (12 digits) and IAM principal that can create VPC, ECS,
      Aurora, ElastiCache, ECR, ALB, Secrets Manager, IAM, CloudWatch
- [ ] `aws sts get-caller-identity` works (`AWS_PROFILE=janus` recommended)
- [ ] Docker can build `linux/amd64` images if you build from ARM (DGX Spark) —
      use `docker build --platform linux/amd64` (see §6)
- [ ] You accept staging cost (NAT + Aurora Serverless v2 + Fargate + Redis;
      typically tens of USD/day while running)
- [ ] `registry/environments/staging.yaml` is present (shipped in this repo)

---

## 1. Prerequisites

- AWS account + IAM permissions listed above
- Access key **or** SSO for that principal
- Docker (build + push)
- Terraform ≥ 1.5
- This repository checked out

Aurora PostgreSQL 16 with the `vector` extension is required; Terraform uses
Aurora Serverless v2. Migration `0004` runs `CREATE EXTENSION IF NOT EXISTS vector`.

---

## 2. Configure credentials (do this locally)

### Option A — AWS CLI profile (recommended)

```bash
aws configure --profile janus
# Access Key ID / Secret / region (e.g. us-east-1) / output json

export AWS_PROFILE=janus
export AWS_ACCOUNT_ID=123456789012   # your account
aws sts get-caller-identity
```

Confirm the returned `Account` matches `AWS_ACCOUNT_ID`.

### Option B — environment variables (session only)

```bash
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
export AWS_ACCOUNT_ID=123456789012
aws sts get-caller-identity
```

Unset when finished: `unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY`.

---

## 3. One-time bootstrap (remote Terraform state)

`./setup.sh --aws` can do this interactively. Manual equivalent:

```bash
ACCOUNT=$AWS_ACCOUNT_ID
REGION=us-east-1

aws s3 mb "s3://janus-tfstate-${ACCOUNT}" --region "$REGION"
aws s3api put-bucket-versioning \
  --bucket "janus-tfstate-${ACCOUNT}" \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption \
  --bucket "janus-tfstate-${ACCOUNT}" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

aws dynamodb create-table \
  --table-name janus-tf-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$REGION"
```

Uncomment the `backend "s3"` block in [`infra/aws/versions.tf`](../infra/aws/versions.tf)
and set `bucket = "janus-tfstate-<account-id>"`. Then:

```bash
cd infra/aws
terraform init -reconfigure
```

Until the backend is enabled, Terraform stores state **locally** under
`infra/aws/` (fine for a first personal apply; not fine for a shared team).

---

## 4. Fill Terraform variables

```bash
cd infra/aws
cp terraform.tfvars.example terraform.tfvars
# Edit:
#   aws_account_id      = "123456789012"
#   aws_region          = "us-east-1"
#   environment         = "staging"
#   name_prefix         = "janus"
#   acm_certificate_arn = ""          # empty = HTTP-only ALB for smoke
#   enable_gpu_eks      = false
```

`terraform.tfvars` is gitignored. **No access keys in this file.**

---

## 5. Plan and apply

```bash
cd infra/aws
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

Capture outputs:

```bash
terraform output
export ALB=$(terraform output -raw alb_dns_name)
export CLUSTER=$(terraform output -raw ecs_cluster)   # e.g. janus-staging
export REGION=$(terraform output -raw region)
export ACCOUNT=$(terraform output -raw account_id)
```

First apply often takes **20–40 minutes** (Aurora + NAT + ECS).

---

## 6. Build and push images

ECS tasks pull from ECR. Until you push, services stay unhealthy / crash-looping.

```bash
cd ../..   # repo root
PREFIX="${CLUSTER}"   # same as terraform output ecs_cluster, e.g. janus-staging

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin \
      "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

# From an ARM host (DGX Spark), target Fargate amd64:
PLATFORM=linux/amd64

for svc in gateway api web; do
  REPO="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${PREFIX}/${svc}"
  if [ "$svc" = "web" ]; then
    docker build --platform "$PLATFORM" -t "${REPO}:latest" apps/web
  else
    docker build --platform "$PLATFORM" -f "services/${svc}/Dockerfile" \
      -t "${REPO}:latest" .
  fi
  docker push "${REPO}:latest"
done
```

Force a new deployment:

```bash
for svc in gateway api web; do
  aws ecs update-service \
    --cluster "$CLUSTER" \
    --service "${CLUSTER}-${svc}" \
    --force-new-deployment \
    --region "$REGION"
done
```

Watch:

```bash
aws ecs describe-services --cluster "$CLUSTER" --region "$REGION" \
  --services "${CLUSTER}-api" "${CLUSTER}-gateway" "${CLUSTER}-web" \
  --query 'services[].{name:serviceName,running:runningCount,desired:desiredCount,rollout:deployments[0].rolloutState}'
```

---

## 7. Run database migrations

Migrations must run **once** against the Aurora writer before the API is useful.
App tasks use the RLS role `janus_app`; migrations use the owner URL from Secrets Manager.

### Option A — one-off ECS task (preferred)

Use the **api** task definition, same private subnets and security group as the
api service, override the command to `["alembic", "upgrade", "head"]`, and set
the same secrets as the api task (`JANUS_MIGRATION_DATABASE_URL`,
`JANUS_APP_DB_PASSWORD`, …). Console: ECS → Task definitions → api →
**Deploy** → **Run task**. CLI shape:

```bash
# Fill NETWORK_CONFIG from the running api service (subnets + sg).
aws ecs run-task \
  --cluster "$CLUSTER" \
  --launch-type FARGATE \
  --task-definition "${CLUSTER}-api" \
  --network-configuration "$NETWORK_CONFIG" \
  --overrides '{"containerOverrides":[{"name":"api","command":["alembic","upgrade","head"]}]}' \
  --region "$REGION"
```

Exact task-definition family name: check
`aws ecs list-task-definitions --family-prefix "${CLUSTER}"`.

### Option B — bastion / VPN

With network reachability to Aurora and secrets loaded (do not print them):

```bash
alembic upgrade head   # from services/api with JANUS_MIGRATION_DATABASE_URL set
```

---

## 8. Smoke test

ALB routes:

- `/` → **web**
- `/v1/*`, `/healthz`, `/readyz`, `/docs` → **api**
- Gateway is **not** on the ALB (api calls it over private DNS)

```bash
curl -fsS "http://${ALB}/healthz"
curl -fsS "http://${ALB}/readyz"
# Browser: http://$ALB — register a workspace and chat (mock model).
```

Logs:

```bash
aws logs tail "/ecs/${CLUSTER}/api" --follow --region "$REGION"
aws logs tail "/ecs/${CLUSTER}/gateway" --follow --region "$REGION"
aws logs tail "/ecs/${CLUSTER}/web" --follow --region "$REGION"
```

For HTTPS: set `acm_certificate_arn` to a cert in the **same region** as the ALB,
`terraform apply` again, then put Route 53 (or CloudFront) in front.

More ops detail: [runbooks/troubleshooting.md](./runbooks/troubleshooting.md).

---

## 9. What this stack creates

| Resource | Purpose |
|----------|---------|
| VPC + public/private/data subnets + NAT | Network isolation |
| ALB | Web `/` and API `/v1/*` (and health) |
| ECS Fargate | `web`, `api`, `gateway` (gateway internal-only) |
| Aurora PostgreSQL 16 Serverless v2 | RLS + pgvector |
| ElastiCache Redis | Shared rate limits / cache |
| ECR | Images |
| Secrets Manager | DB URLs, gateway token, Redis URL |
| S3 | Attachments |
| Optional EKS (`enable_gpu_eks`) | Phase 8 GPU control plane only |

---

## 10. Cost and safety notes

- Staging defaults are small (`cache.t4g.micro`, Aurora min ACU, one NAT).
- `deletion_protection` is on for `prod` only.
- GPU EKS is **off**. Turning it on without node groups still costs a control plane.
- Prefer IAM Identity Center / OIDC for CI over long-lived access keys.
- Tear down when idle: `terraform destroy` (§11) — NAT + Aurora dominate cost.

---

## 11. Tear down

```bash
cd infra/aws
terraform destroy
# Empty and delete the tfstate bucket only when you are sure.
```

---

## After staging works

1. Add cloud provider secrets (OpenAI / Anthropic / …) to Secrets Manager and
   enable matching deployment keys in `registry/environments/staging.yaml`
   (rebuild/push **gateway** image so the registry files ship).
2. Set `acm_certificate_arn` and DNS for HTTPS.
3. For production: new `environment = "prod"` (or separate state), keep
   `registry/environments/prod.yaml` empty until you deliberately enable models.
4. Marketplace listing: [marketplace.md](./marketplace.md).
