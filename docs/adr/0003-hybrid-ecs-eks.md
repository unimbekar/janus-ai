# ADR 0003 — Hybrid ECS (core) + EKS (GPU only)

**Status:** Proposed · **Date:** 2026-08-13 · **Deciders:** Principal Architect, DevOps Engineer, ML Infrastructure Engineer

## Context

The platform has two very different compute profiles. The core services (web, API, gateway, workers) are stateless containers behind a load balancer. GPU model serving needs device plugins, GPU-aware scheduling, multi-GPU topology, long cold starts, warm pools, scale-to-zero, and a controller reconciling desired model deployments to running replicas.

Kubernetes solves the second set well. It adds permanent operational cost — cluster upgrades, add-on lifecycle, node management, RBAC surface — that the first set does not need.

## Decision

Run core services on **ECS Fargate**. Introduce **EKS only for GPU model serving**, and only in Phase 8 when Janus actually hosts models. Until then no EKS cluster exists.

The gateway's deployment abstraction means core services never know which orchestrator serves a model, so this boundary can move later without application changes.

## Consequences

**Positive:** no cluster operations during Phases 1–7, when the priority is product; Kubernetes complexity arrives only with a workload that justifies it; blast radius between core and GPU planes is naturally separated; GPU node pools can be tuned aggressively without risking the core platform.

**Negative:** two deployment models to build and document (ECS task definitions and Kubernetes manifests), two sets of CI/CD paths, and two networking configurations; engineers need familiarity with both.

**Neutral:** if the industry or the team consolidates on Kubernetes later, migrating the core to EKS is straightforward because services are already stateless containers.

## Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| EKS for everything from Phase 1 | Buys nothing for a FastAPI service; imposes cluster operations during the phases that most need velocity |
| ECS for everything including GPU | Fighting GPU scheduling, cold starts, and warm pools that the Kubernetes ecosystem already handles |
| SageMaker endpoints only for hosted models | Less control over runtime choice (vLLM/SGLang), batching, and cost; harder to express warm pools and multi-model packing |
| Managed inference vendor instead of self-hosting | Contradicts sovereign mode and private deployment guarantees |
