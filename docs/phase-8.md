# Phase 8 — As Built

**Status:** foundation only · **Last updated:** 2026-08-14

One sentence: GPU serving is a registry + adapter problem the core already
understands; the EKS cluster is opt-in Terraform and is **off**.

## What shipped

- vLLM / SGLang adapters and a hosted Llama catalog example.
- `enable_gpu_eks` in Terraform creates an EKS control plane only — no GPU node
  groups, no DCGM, no scale-to-zero.

## Honest deferrals

- No production GPU traffic, no cost-per-token dashboards, no Karpenter.
- Do not enable `enable_gpu_eks` without a capacity and budget review.
