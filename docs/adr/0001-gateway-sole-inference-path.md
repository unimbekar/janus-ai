# ADR 0001 — Model Gateway is the sole inference path and a security boundary

**Status:** Proposed · **Date:** 2026-08-13 · **Deciders:** Principal Architect, AI Platform Architect, Security Architect

## Context

Janus will integrate many model providers (Sarvam, OpenAI, Anthropic, Gemini, Bedrock) and several self-hosted runtimes (vLLM, SGLang, Ollama, llama.cpp, MLX). Product features — chat, agents, RAG, evaluation — all need inference.

The common failure mode in platforms like this is provider SDK calls spreading through feature code. Once that happens, provider-specific branching appears everywhere, credentials spread across services, cost tracking becomes partial, policy enforcement becomes advisory, and swapping a provider becomes a migration project rather than a configuration change.

Janus also forwards customer data to third parties, so there must be exactly one place where "what leaves Janus, to whom, with what" is decided and audited.

## Decision

All inference flows through `janus-gateway`. Specifically:

1. Provider SDKs may be imported **only** in `services/gateway/app/backends/**`, enforced by import-linter in CI.
2. Every other component — including the AI Runtime, LangGraph nodes, workers, and the evaluation harness — calls the gateway over HTTP using the OpenAI-compatible contract.
3. Provider credentials are readable only by the gateway's task role from AWS Secrets Manager.
4. The gateway owns, in this fixed order: authentication, authorization, rate limiting, data classification, policy enforcement, routing, health gating, execution with fallback, streaming, metering, and observability.
5. Evaluation runs through the gateway so measurements reflect production behavior.

## Consequences

**Positive**

- Provider independence is mechanically enforced, not merely documented.
- One place to enforce policy, one place to meter cost, one place to explain routing.
- Credential blast radius limited to a single service.
- Adding a provider or runtime is an adapter plus a registry entry.
- Evaluation measures the real path, including gateway overhead.

**Negative**

- One extra network hop, and therefore a latency budget to defend (< 25 ms p95 added, measured per release).
- The gateway is a platform-wide dependency: it must be stateless, multi-AZ, and independently scalable.
- Feature teams cannot reach for a provider's newest SDK feature directly; it must be expressed as a capability first. This is intended friction.

**Mitigations**

- Stateless design, registry and health cached in Redis, autoscaling on active stream count.
- Capability negotiation so provider-specific features are exposed deliberately rather than ad hoc.
- Adapter conformance suite so abstraction leaks surface as test failures.

## Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| Client library imported by each service | No central policy, metering, or egress control; credentials spread; drift between services |
| LangChain adapters used directly in feature code | LangChain becomes the abstraction, tying routing and policy to a third-party library's model of the world |
| Third-party AI gateway (commercial or OSS) | Routing, policy, and multi-tenancy requirements are core differentiators; an external gateway would still need a Janus layer for classification, sovereign mode, and explainability |
| Sidecar per service | Duplicates credential access and complicates policy versioning |
