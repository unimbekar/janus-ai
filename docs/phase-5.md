# Phase 5 — As Built

**Status:** complete (governed loop, not a full LangGraph product) · **Last updated:** 2026-08-14

One sentence: agents are versioned, published, and run through a checkpointed
retrieve → tool → compose loop; every model call still goes through the gateway;
`/v1/responses` is the OpenAI-shaped entry.

## What shipped

- Schema `agent.*` (migration `0004`): agents, versions, runs, steps, checkpoints,
  tools, MCP server rows (tables exist; MCP client is not wired).
- `AgentService`: create / publish / run. Native tools: `clock`, `knowledge_search`.
- `/v1/agents`, `/v1/agents/{id}/publish`, `/v1/agents/{id}/runs`, `/v1/responses`.
- Web: `/agents` to create, publish, and run.
- Checkpoints store messages + output per compose step. Chain-of-thought is not
  stored; tool output is JSON status, not a hidden scratchpad.

## Honest deferrals

- This is a **constrained loop**, not LangGraph. Resume-from-arbitrary-node,
  human-in-the-loop approval, MCP, and multi-agent delegation are not implemented.
- Graph nodes never name a provider (ADR 0001 still holds).
