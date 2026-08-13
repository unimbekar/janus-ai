# ADR 0004 — AI Runtime starts as a library inside `janus-api`

**Status:** Proposed · **Date:** 2026-08-13 · **Deciders:** Principal Architect, Staff Full-Stack Engineer

## Context

The AI Runtime orchestrates chat and agent execution with LangChain and LangGraph. It could be a library inside `janus-api` or a separate `janus-runtime` service.

Agent runs are long, sometimes minutes, with tool calls and human-in-the-loop pauses — a scaling profile unlike ordinary API requests. That argues for separation. But an extra service means an extra hop, extra IAM surface, extra deployment complexity, and harder local development, during phases where product iteration speed matters most.

The engineering rules forbid creating microservices without justification.

## Decision

The runtime is a library (`packages/janus-runtime`) hosted inside `janus-api` for Phases 1–5. Long-running and asynchronous agent runs execute in `janus-worker` via SQS using the same library.

Extraction to a separate service happens when a **trigger condition** is met:

1. Agent runs regularly exceed the interactive request lifetime and workers cannot absorb them, or
2. Runtime workload forces `janus-api` autoscaling decisions that harm API latency, or
3. Runtime dependencies conflict materially with the control plane's.

The library is written with extraction in mind: no direct database access outside repository interfaces, no framework globals, gateway access via an HTTP client, and a stable facade so `janus-api` depends on an interface rather than LangGraph internals.

## Consequences

**Positive:** fewer moving parts; simple local development; no added latency; one deployment for Phases 1–5; the same code path serves inline and worker execution.

**Negative:** the API service carries heavier dependencies (LangChain, LangGraph); a runaway agent run could consume API capacity, mitigated by budgets, step limits, and routing long runs to workers; extraction later is real work, though bounded by the facade.

**Neutral:** the LangChain/LangGraph facade is worth having regardless, since it also protects against framework churn.

## Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| Separate `janus-runtime` service from Phase 1 | Premature; costs velocity and adds a service without evidence of need |
| Runtime inside `janus-gateway` | Conflates orchestration with the security and policy boundary; the gateway must stay thin and fast |
| No facade, LangGraph used directly in feature code | Framework churn would propagate into the domain |
