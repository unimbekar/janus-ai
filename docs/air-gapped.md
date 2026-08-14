# Air-gapped and offline sales

**Status:** offer design + technical constraints · **Last updated:** 2026-08-14

How to sell and deliver Janus when the customer network **cannot** reach the public internet (or must not send prompts to external AI providers).

Related: [sales.md](./sales.md) · [architecture.md](./architecture.md) · [security.md](./security.md) · [model-gateway.md](./model-gateway.md) · [aws-deploy.md](./aws-deploy.md)

---

## 1. What “air-gapped” means here

| Level | Meaning | Janus mode |
|-------|---------|------------|
| **A — Private cloud** | VPC has egress; prompts must not hit OpenAI/Anthropic/etc. | `private` (Janus-hosted / customer GPU / local) |
| **B — Restricted egress** | Allowlisted package mirrors / model registries only | `private` or `sovereign` + network policy |
| **C — True air gap** | No outbound internet from the AI enclave | `offline` — **local / on-prem inference only** |

Most “air-gapped” RFPs are **B or C**. Level A is common on AWS and is *not* a full air gap — sell it as **private mode**, not air-gapped.

**Janus `offline` execution mode:** external providers denied, Janus-hosted cloud denied, **local deployments only** (Ollama, llama.cpp, MLX, customer vLLM on the same enclave). If no local deployment is eligible, the gateway returns `no_eligible_model` rather than leaking to the cloud.

---

## 2. Is this sellable?

**Yes, as a high-touch SKU**, not as a one-click Marketplace SaaS.

Air-gapped buyers pay for:

1. Software that runs entirely inside their boundary  
2. Install / upgrade without internet  
3. Proof that inference cannot egress  
4. Support that matches their change windows  

You already have the architectural hooks (`offline` / `private` modes, local adapters, Compose and container images). You do **not** yet have a turnkey “air-gap appliance ISO” with customer-certified egress tests — price the gap as **delivery engagement**.

---

## 3. Ideal customer profile

| Fit | Examples |
|-----|----------|
| Strong | Defense-adjacent contractors, critical infrastructure, finance secure enclaves, healthcare on-prem, research labs with DGX/air-gapped GPU racks |
| Medium | AWS Gov-ish or isolated VPC with no NAT (still need offline media for images) |
| Poor | Teams that still want GPT-4o quality with zero local GPUs |

**Hard requirement for Level C:** customer provides **GPU (or enough CPU) and open-weight models** they are licensed to run. Janus does not invent model weights inside the gap.

---

## 4. Product promise (what you may claim)

### You may claim

- All chat / agent / knowledge traffic stays on infrastructure they operate when mode is `offline` (or `private` with only private/local deployments enabled).  
- The Model Gateway is the sole inference path; feature code does not call provider SDKs.  
- Catalog and routing hide ineligible cloud models under restrictive modes.  
- Deliverable as container images + Postgres (+ optional Redis) runnable via Compose or customer orchestrator.  
- Structured logs omit prompts/completions (operational honesty).  

### You must not claim (unless proven in *their* install)

- “Certified air-gapped” by a third party  
- FedRAMP / IL5 / Secret classification  
- Specific tokens/sec or quality vs GPT-4  
- Automatic model updates without a media sneakernet process you define  

---

## 5. Reference architecture (Level C)

```text
[ Operator laptop / bastion ] --sneakernet--> [ Air-gapped enclave ]
                                                    │
                                    ┌───────────────┼───────────────┐
                                    ▼               ▼               ▼
                               janus-web       janus-api      janus-gateway
                                    │               │               │
                                    └─────── Postgres (+ Redis) ────┘
                                                    │
                                                    ▼
                                         Local inference only
                                    Ollama / vLLM / llama.cpp / MLX
```

No NAT gateway, no public ALB to the internet, no provider API keys in Secrets.

**AWS note:** True air gap is rarely “Fargate in a normal commercial account.” Typical patterns:

- Customer’s **disconnected** region / SC2S-style / on-prem Kubernetes / bare metal DGX  
- Or **isolated VPC** with no egress (closer to Level B) still on AWS  

Marketplace Container helps **acquire** the software; **fulfillment** for Level C is usually offline media + SOW, not “subscribe and pull from public ECR.”

---

## 6. Bill of materials (what you deliver)

| Artifact | Purpose |
|----------|---------|
| Container images (`web`, `api`, `gateway`) | Signed digests; transfer via USB / customer registry sync |
| `registry/` YAML | Only local / private deployments enabled for that env overlay |
| Postgres (+ Redis) images or customer-managed DB | Same schema / migrations |
| Open-weight model files | Customer-licensed; hash-pinned in registry |
| Install runbook | Offline `docker load` / private registry push, migrate, smoke |
| Egress test script | Fail if gateway can resolve/reach public AI endpoints |
| Support terms | How patches arrive (periodic media, not live internet) |

Suggested env overlay concept: `registry/environments/airgap.yaml` with **only** local deployment keys enabled (customer-specific; do not ship cloud keys in that overlay).

---

## 7. Sales motion

```text
1. Qualify air-gap level (A / B / C) and available GPUs + model licenses
2. Architecture workshop (1–2 days) — network, IdP (often local), storage
3. Fixed-price pilot on a representative enclave (or staging “egress denied” VPC)
4. Egress attestation — customer or you run the deny-list test
5. License + support + update cadence SOW
6. Production cutover on their change calendar
```

### Pricing sketch

| Component | Model |
|-----------|--------|
| Software | Annual license (by seats, orgs, or GPU nodes) |
| Install | One-time professional services |
| Updates | Quarterly media + optional on-site |
| Extra models | Pass-through of their licensing; optional packing fee |

Air-gapped deals are **higher ACV, longer cycle** than Marketplace self-serve.

---

## 8. Demo script (air-gapped)

1. Show org default mode set to `offline` (or API key `mode_ceiling: offline`).  
2. Catalog shows only local models.  
3. Chat works against Ollama / mock.  
4. Attempt to force a cloud model → policy / not found.  
5. Disconnect DNS/NAT (or show no provider credentials) and repeat.  
6. Agent + knowledge still work with local embeddings / hash embed fallback as appropriate.  

If you cannot demo without internet, do not sell Level C yet — sell Level A/B private VPC first.

---

## 9. Technical checklist before you sign Level C

- [ ] Customer GPU/CPU sized for chosen weights  
- [ ] Model license allows their commercial use  
- [ ] Images build offline-loadable (`docker save` / OCI layout)  
- [ ] Migrations run without pulling from the internet  
- [ ] `offline` (or equivalent policy) enforced on org + keys  
- [ ] Registry overlay excludes all `privacy: provider` deployments  
- [ ] Egress test in contract acceptance criteria  
- [ ] Patch/update process agreed (who builds media, how often)  
- [ ] Backup/restore of Postgres without cloud DR assumptions  
- [ ] Support channel that works without Slack-on-internet (phone, on-prem ticket)  

---

## 10. Risks and honest mitigations

| Risk | Mitigation |
|------|------------|
| Model quality gap vs frontier cloud | Set expectations; evaluate on *their* tasks; optional dual-enclave (classified offline + open cloud) |
| Stale models | Contracted update cadence; hash verification on load |
| Support burden | Limit SKUs; certified hardware profiles (e.g. DGX Spark / specific GPU class) |
| Customer thinks “AWS Marketplace = works offline” | Explain: Marketplace for contracting; air-gap fulfillment is offline delivery |
| Accidental egress via dependency download | Pin images; deny outbound; vendor build pipeline outside the gap |

---

## 11. One-pager for the customer

**Janus Air-Gapped** puts the Janus AI control plane — chat, agents, knowledge, and an OpenAI-compatible API — entirely inside your security boundary. Inference uses only models you host (Ollama, vLLM, llama.cpp, …). Organizational `offline` mode refuses external providers. We deliver signed containers and an install/update process that does not require internet from the enclave. You provide GPUs and model licenses; we provide the platform, policy boundary, and support.

For connected private VPC (egress allowed but providers forbidden), ask about **Janus Private** instead of full air gap.
