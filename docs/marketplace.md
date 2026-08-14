# AWS Marketplace listing guide

**Status:** preparation guide · **Last updated:** 2026-08-14

Goal: list Janus Intelligence on AWS Marketplace so customers can subscribe from
their AWS account. This is **seller onboarding + listing prep**, not a guarantee
that the listing is live next week.

Honest timeline: new seller verification and the first listing review commonly
take **2–8 weeks**, depending on tax/identity checks, product type, and AWS
review queues. You can prepare everything this week; go-live is AWS-gated.

**Do not put AWS access keys in the listing, screenshots, or this repository.**

Related: [aws-deploy.md](./aws-deploy.md) · [aws.md](./aws.md)

---

## 1. Choose a product type

| Type | When to use | Fit for Janus |
|------|-------------|---------------|
| **Container** | Customer runs your images in their account (ECS/EKS) | Strong first listing: ECR images + CloudFormation/Terraform delivery |
| **SaaS** | You host; they subscribe and federate | Matches Janus-as-a-service; needs metering API + landing page + registration |
| **AMI** | Full VM image | Not the primary path |

**Recommendation for a near-term listing:** ship a **Container** product that
deploys the Phase 7 stack (or a documented subset) into the buyer’s account.
Add a **SaaS** listing once registration, metering, and support SLAs are ready.

---

## 2. Seller account checklist (start immediately)

1. Create or designate an AWS account for Marketplace **seller** operations
   (often separate from production).
2. Open [AWS Marketplace Management Portal](https://aws.amazon.com/marketplace/management/).
3. Complete seller registration:
   - Legal business name and address
   - Tax interview (W-9 / equivalent)
   - Bank account for disbursements
   - Public profile (logo, support email, website)
4. Wait for seller verification email/status before submitting a product.

If seller verification is still pending, you can still prepare assets below.

---

## 3. Assets to prepare this week

| Asset | Notes |
|-------|-------|
| Product title & short description | “Janus Intelligence — provider-independent AI platform” |
| Long description | Chat, agents, knowledge, OpenAI-compatible gateway, private/local modes |
| Logo / hero images | Follow Marketplace dimension guidelines |
| Support email + URL | Must be monitored |
| EULA / terms | Your counsel; AWS also has standard templates |
| Pricing | Free trial, hourly, or monthly — decide before submit |
| Architecture diagram | From [aws.md](./aws.md) |
| Deployment guide | Link or attach [aws-deploy.md](./aws-deploy.md) |
| Container images | Gateway, API, web in ECR (public or Marketplace-managed) |
| Security questionnaire | Encryption, tenancy (RLS), secrets handling, no customer data in logs |

---

## 4. Container product path (fastest technical path)

1. Build and scan images (`make images`, ECR scan on push is already in Terraform).
2. Publish usage instructions that call for:
   - Buyer provides VPC or uses the included Terraform
   - Buyer stores secrets in Secrets Manager
   - Buyer runs `alembic upgrade head` once
3. **Customer entry point:** ship / document `./setup.sh` as the one-step installer:
   - `./setup.sh --local --yes` — eval on any Linux box with Docker
   - `./setup.sh --aws` — AWS CLI, Terraform, tfvars, optional apply
4. Optional: wrap `infra/aws` as a CloudFormation custom resource or document
   “apply Terraform from the seller-provided package”.
5. Submit the Container product in the Management Portal with pricing and
   fulfillment options.
6. Respond to AWS reviewer questions promptly (common delays live here).

### Buyer quick start (copy into listing)

```bash
# After downloading / cloning the Janus package:
chmod +x setup.sh
./setup.sh
# Choose: 1) Local  2) AWS  3) Tools only
```

Silent local eval:

```bash
./setup.sh --local --yes
# Open the URL the script prints → Create workspace → Chat
```

AWS into the subscriber account:

```bash
./setup.sh --aws
# Follow prompts for region + account; keys via aws configure (never in git)
# Full steps: docs/aws-deploy.md
```

---

## 5. SaaS product path (hosted Janus)

Required beyond containers:

- Customer registration landing page that accepts the Marketplace subscription
  token
- Resolve customer ↔ AWS account mapping
- [AWS Marketplace Metering Service](https://docs.aws.amazon.com/marketplace/latest/userguide/saas-product-integration.html)
  for usage dimensions (e.g. requests, seats)
- Entitlement checks on API key creation / org activation
- Private offer workflow if you sell enterprise contracts

Do not claim SaaS metering is implemented in this repo until the metering
integration and entitlement checks are coded and tested.

---

## 6. What “launch next week” can realistically mean

| Done by you next week | Controlled by AWS |
|-----------------------|-------------------|
| Seller registration submitted | Seller verification approval |
| Staging deploy in your account | Listing review approval |
| Images, docs, pricing draft | Search ranking / category placement |
| Security questionnaire filled | Private offer paperwork if needed |

Treat Marketplace as a **parallel track** to a direct staging URL you can demo
immediately after `terraform apply`.

---

## 7. Operational requirements after listing

- Monitor the support alias daily
- Publish a status page or email path for incidents
- Keep the public registry and docs honest: no fabricated benchmarks, no
  “SOC 2 certified” until the report exists
- Rotate credentials; never embed long-lived keys in images

---

## 8. Suggested order of operations

1. `make check` green on `main`
2. Deploy staging with [aws-deploy.md](./aws-deploy.md)
3. Start Marketplace seller registration **today**
4. Prepare Container listing assets while verification runs
5. Submit listing when seller status is approved
6. Only then market a Marketplace URL
