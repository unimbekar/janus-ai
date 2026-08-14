# Phase 4 — As Built

**Status:** complete (local/hosted adapters + hardware metadata) · **Last updated:** 2026-08-14

One sentence: the same gateway can dispatch to vLLM, SGLang, llama.cpp, and MLX;
deployments carry hardware metadata; warming deployments are not given production
traffic.

## What shipped

- Adapters: `vllm`, `sglang`, `llamacpp`, `mlx` (OpenAI-compatible HTTP, same as
  the existing generic adapter, distinct backend ids for registry clarity).
- `DeploymentRecord.hardware` and public `accelerator` on catalog summaries.
  Endpoints and credentials still never leave the gateway.
- Example hosted catalog entry `janus/llama3.1-8b-hosted` (vLLM). **Not enabled**
  in local/test overlays — enable the deployment key when a private endpoint exists.
- Usage UI `/usage` lists deployments this workspace can see.
- Warming/offline deployments are excluded from routing (`HealthState.is_routable`).

## Honest deferrals

- No live GPU fleet. There is no warm-pool controller beyond health probes.
- `private` mode already existed; this phase does not add a new execution plane
  until a Janus-hosted deployment is enabled.

## Tests

Registered backend ids; warming exclusion; catalog still hides endpoints.
