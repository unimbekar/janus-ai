# Phase 7 — As Built

**Status:** Terraform + runbooks complete; apply is on your AWS account · **Last updated:** 2026-08-14

One sentence: a Fargate + Aurora + Redis + ALB stack lives in `infra/aws`, with
a runbook that uses the AWS CLI — never committed keys.

## What shipped

- `infra/aws`: VPC, ALB, ECS Fargate (web/api/gateway), Aurora PostgreSQL 16
  Serverless v2, ElastiCache Redis, ECR, Secrets Manager, S3, optional EKS stub.
- [aws-deploy.md](./aws-deploy.md): configure credentials, bootstrap state,
  plan/apply, push images, migrate, smoke.
- CI still builds images; it does not apply Terraform (no long-lived keys in GitHub).

## Honest deferrals

- No rehearsed restore drill, no load-test numbers, no WAF/CloudFront module.
- Staging/prod apply is operator-driven until OIDC roles exist.
- `terraform apply` is **not** run from this development host unless you configure
  AWS credentials locally.
