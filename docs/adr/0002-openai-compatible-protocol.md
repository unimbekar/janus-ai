# ADR 0002 — OpenAI-compatible protocol as internal and external lingua franca

**Status:** Proposed · **Date:** 2026-08-13 · **Deciders:** Principal Architect, AI Platform Architect

## Context

Janus needs one wire format between the runtime and the gateway, between the gateway and self-hosted runtimes, and between customer applications and Janus. Candidate formats are a Janus-native protocol, an existing framework's message format, or the OpenAI API shape.

vLLM, SGLang, Ollama, and llama.cpp all already expose OpenAI-compatible endpoints. LangChain, LangGraph, and the official OpenAI SDKs all speak it. Several cloud providers offer compatibility layers.

## Decision

Adopt the OpenAI API shape as the protocol at three boundaries: the public inference API, the runtime-to-gateway call, and the gateway-to-self-hosted-runtime call. Janus-specific behavior is carried in a namespaced `janus` object in requests and responses, which OpenAI-compatible clients ignore safely.

Native provider protocols (for example Anthropic's) are handled inside their adapters and normalized to the common shape.

A Janus-native superset endpoint (`/v1/responses`) is added in Phase 5 for agentic features that do not fit chat completions, without breaking the compatibility surface.

## Consequences

**Positive:** existing tooling works unmodified, which lowers adoption friction for customers and for our own engineers; self-hosted runtimes need no bespoke integration; SDK generation is straightforward.

**Negative:** the format is not ours, so it evolves outside our control and does not natively express everything we need (routing hints, classification, agent context) — hence the `janus` namespace; some provider capabilities need normalization work in adapters, and mismatches must be surfaced as capability metadata rather than hidden.

**Neutral:** we are not bound to OpenAI as a vendor; we are borrowing an interface shape that has become a de facto standard.

## Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| Janus-native protocol only | Loses free interoperability with LangChain, SDKs, and self-hosted runtimes; every integration becomes bespoke work |
| LangChain message format as the wire format | Couples the public API to a framework's internals and its release cadence |
| Per-provider passthrough | Reintroduces vendor coupling in callers, contradicting [ADR 0001](./0001-gateway-sole-inference-path.md) |
