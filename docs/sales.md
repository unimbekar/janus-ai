# Selling Janus Intelligence

**Status:** sales playbook · **Last updated:** 2026-08-14  
**Audience:** founders / GTM · not a legal contract

Related: [marketplace.md](./marketplace.md) · [aws-deploy.md](./aws-deploy.md) · [architecture.md](./architecture.md) · [air-gapped.md](./air-gapped.md) · [security.md](./security.md)

---

## 1. Can you sell this on AWS Marketplace?

**Yes — as a Container (and later SaaS) product — if you sell what is true today and price for early customers.**

Janus is a real, runnable AI control plane: chat, agents, knowledge (text RAG), OpenAI-compatible gateway, org tenancy with RLS, and Terraform for ECS Fargate. That is a legitimate Marketplace listing category.

It is **not** yet a finished “enterprise compliance appliance.” Do not sell SOC 2, FedRAMP, HIPAA BAA, full SSO/SCIM, or a live Janus GPU fleet as shipped features. Those are roadmap or customer-specific engagements.

| Question | Honest answer |
|----------|----------------|
| Is there a product people can use? | Yes — local Compose and AWS Fargate path |
| Is Marketplace listing live? | No until seller verification + AWS review ([marketplace.md](./marketplace.md)) |
| Who buys first? | Teams that want one API over many models + private/local options |
| Who waits? | Agencies that need audited compliance certificates on day one |

**Best near-term Marketplace shape:** Container product → buyer deploys in *their* AWS account (Terraform + images). SaaS listing comes after metering, landing page, and support SLAs.

---

## 2. One-sentence pitch

**Janus is the AI operating layer: one chat/agent surface and one OpenAI-compatible API that routes to cloud, private, or local models under your org’s policy — without baking a vendor into your product.**

---

## 3. Who to sell to (ICP)

### Primary (now)

| Buyer | Pain | Why Janus |
|-------|------|-----------|
| **US mid-market eng / platform teams** | Many model SDKs, no single policy, hard to switch providers | Gateway + registry; OpenAI-compatible clients keep working |
| **Product companies adding AI** | Need chat + agents without becoming an ML shop | Chat, agents, knowledge behind one stack |
| **Security-conscious teams** | Cannot send every prompt to OpenAI | `private` / `offline` modes; local Ollama / self-hosted adapters |
| **AWS-native shops** | Prefer buy-in-Marketplace + deploy in own VPC | Container listing + ECS Fargate Terraform |

### Secondary (qualified deals)

| Buyer | Caveat |
|-------|--------|
| Regulated (healthcare, gov-adjacent) | Sell **architecture + engagement**, not “certified.” Pair with their ATO / BAA process |
| Air-gapped / disconnected | Separate offer — see [air-gapped.md](./air-gapped.md) |
| Large enterprise SSO-first | SSO/SCIM not shipped; time-box a Phase 9 add-on or partner IdP |

### Do not chase yet

- Buyers who only want “ChatGPT cheaper”
- Buyers who require SOC 2 Type II report in the RFP appendix this quarter (unless you already have it)
- Buyers who need production Janus-hosted 70B GPU SLAs tomorrow

---

## 4. Value props (claim only what you can demo)

| Claim | Demo / proof |
|-------|----------------|
| Provider-independent | Pin `auto`, then restrict mode; show catalog filtered by policy |
| Explainable routing | UI attribution + `telemetry.routing_decisions` |
| Tenant isolation | RLS tests / two-org demo |
| OpenAI-compatible | `curl` / SDK against `/v1/chat/completions` with `jsk_` key |
| Private / local path | Ollama or mock under `private` / `offline`; no external provider keys |
| Deploy in customer AWS | `infra/aws` + [aws-deploy.md](./aws-deploy.md) |
| Agents + knowledge | Ingest text → agent run with citations |

**Never claim:** fabricated benchmarks, “SOC 2 certified,” “HIPAA compliant,” or “data never leaves your VPC” unless the customer’s deployment mode and network actually enforce it.

---

## 5. Packaging and pricing (starting point)

Decide before Marketplace submit; iterate with design partners.

| SKU | What they get | Suggested framing |
|-----|---------------|-------------------|
| **Janus Community / Trial** | Free tier or 14–30 day trial; mock + their own API keys | Land, prove routing |
| **Janus Cloud (customer VPC)** | Container + Terraform; support business hours | Marketplace Container hourly or monthly |
| **Janus Private** | Same + help wiring vLLM/Ollama; `private` mode defaults | Higher monthly + onboarding |
| **Janus Air-Gapped** | Offline media, local models only — [air-gapped.md](./air-gapped.md) | Fixed license + install engagement |
| **Janus Enterprise** | Custom SSO, policies, residency, dedicated support | Annual contract; not pure self-serve |

Usage metering (tokens / seats) for SaaS Marketplace comes later; Container can start with software fee + customer pays their own model/GPU bills.

---

## 6. Sales process (practical)

```text
1. Qualify          → mode needed? cloud keys OK? air-gap? compliance certificates required?
2. Demo (30 min)    → register → chat Auto → show model attribution → knowledge + agent → /v1 models
3. Pilot (2–4 wk)   → staging in their AWS or your staging ALB; success = N users, 1 agent, 1 KB
4. Security review  → architecture.md + RLS + secrets + no prompts in logs
5. Commercial       → Marketplace subscribe or direct PO
6. Deploy           → aws-deploy.md; handoff runbooks/troubleshooting.md
```

### Discovery questions

1. Where may inference run today — cloud only, private VPC, or air-gapped?
2. Do you already pay OpenAI/Anthropic/Bedrock, or only local GPUs?
3. Must the contract include SOC 2 / HIPAA / FedRAMP *evidence*, or architecture review?
4. OpenAI SDK / LangChain apps that must keep working unchanged?
5. Who owns AWS account and Marketplace subscription — platform or app team?

### Objection handling

| Objection | Response |
|-----------|----------|
| “Just use Bedrock / OpenAI directly” | You still need tenancy, routing, audit, agents, and swap-out. Janus is the control plane |
| “Where is LangChain?” | Inference is gateway-only by design; orchestration is in-product. Clients can still use LangChain *against* Janus |
| “No SOC 2” | Correct today. Sell pilot + control evidence (RLS, modes, logs policy); compliance as a paid workstream |
| “Marketplace isn’t listed yet” | Direct pilot + seller registration in parallel; subscribe when live |
| “Will you lock us in?” | OpenAI-compatible API + registry-as-code; models are adapters |

---

## 7. AWS Marketplace GTM checklist

1. Start seller registration **now** ([marketplace.md](./marketplace.md))  
2. Pick **Container** first  
3. Freeze demo script + screenshots from a clean staging deploy  
4. Publish support email / status path  
5. EULA + pricing with counsel  
6. Security questionnaire from [security.md](./security.md) + troubleshooting honesty  
7. Submit listing; keep selling direct pilots while AWS reviews  

---

## 8. Competitive positioning (short)

| vs | Position Janus as |
|----|-------------------|
| Raw OpenAI / Anthropic | Policy, multi-model, private mode, your UX |
| LangChain alone | Production gateway, tenancy, metering, deployable product |
| Open WebUI / LibreChat | Org policy + routing decision log + AWS packaging |
| Bedrock-only | Multi-provider + local/offline without rewriting apps |

---

## 9. What “good enough to sell” means this quarter

**Sellable now**

- Chat + catalog + Auto routing with explanations  
- Agents + text knowledge + citations  
- OpenAI-compatible API keys  
- Customer-VPC deploy story (Terraform)  
- Clear private/local story with Ollama / self-hosted adapters  

**Sell as roadmap / professional services**

- Marketplace live listing  
- SSO / SCIM / full policy UI  
- SOC 2 Type II  
- Managed Janus GPU fleet with SLAs  
- Air-gapped certified install (see companion doc)  

If a deal needs more than half the “roadmap” list as day-one must-haves, price it as a **custom deployment**, not a self-serve Marketplace click.
