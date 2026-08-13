# ADR 0005 — Shared-schema multi-tenancy with PostgreSQL row-level security

**Status:** Proposed · **Date:** 2026-08-13 · **Deciders:** Principal Architect, Security Architect

## Context

Janus is multi-tenant from day one and stores organizations' confidential conversations, documents, and agent configurations. Cross-tenant leakage is the most severe failure the platform can have.

Options are a shared schema with an `organization_id` column, a schema per tenant, or a database per tenant. Application-level scoping alone is fragile: one forgotten `WHERE organization_id = …` in one query is a breach.

## Decision

Shared schema with `organization_id` on every tenant-scoped table, protected by **two independent layers**:

1. **Application:** a repository layer that scopes every query from the authenticated principal's organization context. The organization is never read from a request body.
2. **Database:** PostgreSQL row-level security with `USING` and `WITH CHECK` policies on every tenant table, `FORCE ROW LEVEL SECURITY`, and an application role without `BYPASSRLS`. The organization is supplied per transaction with `SET LOCAL janus.organization_id`, which is compatible with transaction-level connection pooling.

Object storage uses per-organization prefixes with IAM conditions, Redis keys are namespaced per organization, vector search filters by organization before ANN, and secrets use per-organization paths.

CI fails if a new table with an `organization_id` column lacks an RLS policy, and cross-tenant access tests run per tenant table.

## Consequences

**Positive:** a single missed application-level filter cannot leak data; one schema to migrate rather than thousands; connection pooling stays efficient; onboarding a tenant is a row, not a migration.

**Negative:** RLS adds query planning overhead (small, measured); every session must set the context variable correctly, so the connection-management code is critical and must be tested; developers must understand RLS when debugging "missing" rows.

**Neutral:** the model registry is intentionally platform-scoped, not tenant-scoped ([ADR 0007](./0007-platform-scoped-registry.md)).

## Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| Application-level scoping only | One human error equals a breach; unacceptable for confidential tenant data |
| Schema per tenant | Migration and connection-pool cost grows with tenant count; no better than RLS for the threat we care about |
| Database per tenant | Strongest isolation but operationally prohibitive at scale; reserved for enterprise customers contractually requiring physical separation, served by dedicated deployments |
