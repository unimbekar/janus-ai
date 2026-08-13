# ADR 0007 — Platform-scoped model registry with per-organization visibility

**Status:** Proposed · **Date:** 2026-08-13 · **Deciders:** Principal Architect, AI Platform Architect

## Context

Every other significant entity in Janus is tenant-scoped. The model catalog could follow that pattern — each organization holding its own model records — or be platform-scoped with access governed by policy.

Models and their deployments are operational facts about Janus infrastructure: a vLLM deployment on a GPU node pool exists once, not once per customer. What varies per organization is which models they may *use*, which is a policy question.

## Decision

`registry.models`, `registry.model_deployments`, `registry.model_aliases`, `registry.model_prices`, and `registry.licenses` are **platform-scoped**: readable by all tenants through policy-filtered APIs, writable only by the platform-operator role.

Per-organization differences are expressed as policy ([security.md](../security.md#7-policy-engine)): allow and deny lists for models and providers, region restrictions, execution mode ceilings, and classification rules. `GET /v1/models` returns only what the caller's policy permits.

Registry content is managed as code in `registry/*.yaml`, reviewed in pull requests, and synchronized to the database — so a model change has the same review discipline as code.

Bring-your-own-key (Phase 9 candidate) is modeled as organization-scoped **credentials** attached to an existing platform model, not as a private model record.

## Consequences

**Positive:** one fleet to operate and monitor; onboarding a model benefits every eligible organization at once; health, evaluation results, and pricing exist once and stay consistent; policy remains the single mechanism for "what may this organization use", which is easier to audit than duplicated catalogs.

**Negative:** a truly organization-private model (a customer's own fine-tune) needs an explicit extension — a visibility scope on the model record — which is deliberately deferred until a real requirement exists; `GET /v1/models` must always be policy-filtered, and a bug there is an information disclosure, so it is a required test case.

**Neutral:** deployment endpoints and hardware detail are operator-only regardless of scoping.

## Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| Per-organization model catalogs | Duplicates operational facts; health and pricing drift between copies; policy still needed anyway |
| Registry entirely in configuration files, not the database | Loses joins to usage, routing decisions, health, and evaluation results, and makes admin UI and runtime updates impractical |
| Registry entirely in the database with no code review path | Model onboarding is a governance event ([model-registry.md](../model-registry.md#6-onboarding-pipeline)); it deserves pull-request review |
