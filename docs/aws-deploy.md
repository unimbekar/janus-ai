# Deploy Janus on AWS

**Status:** as-built for Phase 7 · **Last updated:** 2026-08-14

This is the runbook for applying `infra/aws` to **your** AWS account. Architecture
context lives in [aws.md](./aws.md). Marketplace listing steps are in
[marketplace.md](./marketplace.md).

**Never paste access keys into chat, tickets, or git.** Configure the AWS CLI (or
temporary env vars) on the machine that runs Terraform.

---

## 1. Prerequisites

- AWS account ID (12 digits)
- IAM user or role with permission to create VPC, ECS, Aurora, ElastiCache, ECR,
  ALB, Secrets Manager, IAM roles, and CloudWatch Logs
- Access key **or** SSO login for that principal
- Docker (to build and push images)
- Terraform ≥ 1.5
- This repository checked out

**Recommended:** from the repo root run the customer wizard:

```bash
./setup.sh --aws
# installs AWS CLI + Terraform if needed, writes terraform.tfvars,
# optionally bootstraps S3/DynamoDB state and terraform plan/apply
```

On the DGX host, the older developer path still works:

```bash
venv                 # alias → dgx-ai-lab/.venv (Python 3.12)
./install.sh --tools-only
# puts terraform, aws, gh under ~/.local/bin
```

Aurora PostgreSQL 16 with the `vector` extension (pgvector) is required; the
Terraform stack uses Aurora Serverless v2.

---

## 2. Configure credentials (do this locally)

### Option A — AWS CLI profile (recommended)

```bash
aws configure --profile janus
# AWS Access Key ID:     <your key>
# AWS Secret Access Key: <your secret>
# Default region name:   us-east-1
# Default output format: json

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

Unset the variables when finished (`unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY`).

---

## 3. One-time bootstrap (remote Terraform state)

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

Uncomment the `backend "s3"` block in `infra/aws/versions.tf` and set the bucket
name to `janus-tfstate-<account-id>`.

---

## 4. Fill Terraform variables

```bash
cd infra/aws
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars:
#   aws_account_id = "123456789012"
#   environment    = "staging"
#   acm_certificate_arn = ""   # set for HTTPS / production
```

`terraform.tfvars` is gitignored via the usual local-only pattern — do not commit
account-specific files with secrets. There are **no** access keys in tfvars.

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
ALB=$(terraform output -raw alb_dns_name)
```

First apply takes 20–40 minutes (Aurora + NAT + ECS).

---

## 6. Build and push images

```bash
cd ../..   # repo root
REGION=us-east-1
ACCOUNT=$AWS_ACCOUNT_ID
PREFIX=janus-staging   # must match name_prefix-environment

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

for svc in gateway api web; do
  if [ "$svc" = "web" ]; then
    docker build -t "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${PREFIX}/web:latest" apps/web
  else
    docker build -f "services/${svc}/Dockerfile" \
      -t "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${PREFIX}/${svc}:latest" .
  fi
  docker push "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${PREFIX}/${svc}:latest"
done
```

Force a new ECS deployment after the push:

```bash
CLUSTER=janus-staging
for svc in gateway api web; do
  aws ecs update-service --cluster "$CLUSTER" --service "${CLUSTER}-${svc}" --force-new-deployment
done
```

---

## 7. Run database migrations

Migrations must run **once** against the Aurora writer before API traffic is
healthy. Easiest path: one-off ECS task or a bastion with network access to the
data subnets.

```bash
# Example: run the API image with the migrate command, same task role / subnets.
# Pull JANUS_MIGRATION_DATABASE_URL and JANUS_APP_DB_PASSWORD from Secrets Manager.
alembic upgrade head
```

The migration creates the `janus_app` role (RLS-enforced). Application containers
already point `JANUS_DATABASE_URL` at that role.

Enable pgvector is done inside migration `0004` (`CREATE EXTENSION IF NOT EXISTS vector`).
Aurora must allow the extension (PostgreSQL 16 + pgvector is supported on current
Aurora versions).

---

## 8. Smoke test

```bash
curl -fsS "http://${ALB}/healthz"
curl -fsS "http://${ALB}/readyz"
# Open http://$ALB in a browser — register a workspace and send a chat message.
```

For HTTPS, set `acm_certificate_arn` to a certificate in the **same region** as
the ALB, re-apply, and put Route 53 (or CloudFront) in front.

---

## 9. What this stack creates

| Resource | Purpose |
|----------|---------|
| VPC + public/private/data subnets + NAT | Network isolation |
| ALB | Web `/` and API `/v1/*` |
| ECS Fargate services | `web`, `api`, `gateway` |
| Aurora PostgreSQL 16 Serverless v2 | RLS + pgvector |
| ElastiCache Redis | Shared rate limits |
| ECR | Images |
| Secrets Manager | DB URL, gateway token, Redis URL |
| S3 | Attachments |
| Optional EKS (`enable_gpu_eks`) | Phase 8 GPU control plane only |

---

## 10. Cost and safety notes

- Staging defaults are small (`cache.t4g.micro`, Aurora min 0.5 ACU, one NAT).
- `deletion_protection` is on for `prod` only.
- GPU EKS is **off**. Turning it on without node groups still costs a control plane.
- Prefer IAM Identity Center / OIDC for CI over long-lived access keys.

---

## 11. Tear down

```bash
cd infra/aws
terraform destroy
# Empty and delete the tfstate bucket only when you are sure.
```
