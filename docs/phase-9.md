# Phase 9 — As Built

**Status:** control-plane slice · **Last updated:** 2026-08-14

One sentence: usage, audit, and organization policies are queryable APIs; SSO,
SCIM, sovereign egress proofs, and SOC 2 are not done.

## What shipped

- `GET /v1/usage` — org totals from `telemetry.usage_records`.
- `GET /v1/audit-events` — admin/owner session only.
- `GET`/`POST /v1/policies` — org-scoped mode / cost ceiling (most-restrictive
  resolution with API keys already existed).
- Existing RBAC roles (owner/admin/member/viewer) on sessions.

## Honest deferrals

- OIDC/SAML, SCIM, teams UI, policy simulation, customer-managed KMS, HIPAA/BAA,
  and SOC 2 Type II **are not** in this build. Do not claim them in sales material.
