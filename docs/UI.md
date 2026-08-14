# Janus UI — customer presentation

**Status:** as-built product UI · **Last updated:** 2026-08-14  
**Live demo:** local `http://localhost:3010` (or the port `make stack-up` prints)

This document is a **slide-friendly walkthrough** of what a user sees and can do today. Layouts are wireframes of the real Next.js app (`apps/web`), not mockups of a future redesign.

Related: [sales.md](./sales.md) · [architecture.md](./architecture.md) · [api.md](./api.md)

---

## 1. Product surface at a glance

Five primary places after sign-in, always reachable from the top bar:

| Nav | Route | Job |
|-----|-------|-----|
| **Chat** | `/` | Daily conversations with streaming answers + model attribution |
| **Models** | `/models` | Browse what this org is allowed to use |
| **Agents** | `/agents` | Create, publish, and run governed agents |
| **Knowledge** | `/knowledge` | Ingest text, search with scores |
| **Usage** | `/usage` | Token/cost totals + deployment health |

Top-right always shows: **organization name**, **execution mode badge** (e.g. `auto mode`), **Sign out**.

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  [J] Janus     Chat   Models   Agents   Knowledge   Usage      Acme · auto mode · Sign out │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Demo narrative (10 minutes)

Use this order with a prospect:

1. **Register** a workspace → show org + mode badge  
2. **Chat** with Auto → point at “which model answered” while it streams  
3. **Models** → open a model → “Chat with this model”  
4. **Knowledge** → paste a short policy → search  
5. **Agents** → attach that KB → run → show citations  
6. **Usage** → requests/tokens + deployments (no secret endpoints)  

---

## 3. Sign in / register

**Route:** any protected page when logged out  

```text
┌─────────────────────────────┐
│         [J] Janus           │
│                             │
│   Sign in  |  Create workspace │
│                             │
│   Email                     │
│   Password (12+ chars)      │
│   [Name / Org — register]   │
│                             │
│   [ Continue ]              │
└─────────────────────────────┘
```

### Actions

| Action | Result |
|--------|--------|
| Create workspace | User + organization; becomes **owner**; session cookie (HttpOnly) |
| Sign in | Resume existing org context |
| Switch login ↔ register | Same card, no separate marketing site required |

**Talking point:** No provider API keys in the browser. Session stays on the Janus origin; `/api/*` is proxied server-side.

---

## 4. Chat

**Route:** `/` · optional `?c=<conversation_id>` · optional `?model=<slug>`

```text
┌─ top bar ──────────────────────────────────────────────────────────────┐
│ Janus · Chat · …                                          Org · mode   │
├────────────┬───────────────────────────────────────────────────────────┤
│ New chat   │                                                           │
│            │   Empty state: headline + suggestion chips                │
│ Yesterday  │                                                           │
│  · Thread  │   You                                                     │
│  · Thread  │   ┌─────────────────────────────────────────────┐         │
│            │   │ Summarize this contract…                    │         │
│            │   └─────────────────────────────────────────────┘         │
│            │                                                           │
│            │   Assistant                                               │
│            │   [janus/mock-small] [local]  “Selected … because …”      │
│            │   Streaming answer… █                                     │
│            │                                                           │
│            ├───────────────────────────────────────────────────────────┤
│            │  Model [ Auto ▾ ]     [ Stop ]                            │
│            │  ┌─────────────────────────────────────────┐  [ Send ]    │
│            │  │ Message…                                │              │
│            │  └─────────────────────────────────────────┘              │
└────────────┴───────────────────────────────────────────────────────────┘
```

### Actions

| Action | What happens |
|--------|----------------|
| **New chat** | Creates a conversation; URL updates to `?c=cnv_…` |
| **Select thread** | Loads persisted messages for that conversation |
| **Pick suggestion chip** | Fills composer (or sends — same intent: start fast) |
| **Choose model** | `Auto` or pin a catalog model for this turn |
| **Send** | Persists user message; streams assistant SSE; shows routing badge mid-stream |
| **Stop** | Cancels in-flight generation (best-effort) |
| **Read attribution** | Model slug, privacy (e.g. local), human-readable routing explanation |
| **Deep-link from Models** | `/?model=janus/…` pre-selects that model |

**Talking points**

- Answers are **labeled with the model that produced them** — not a black box.  
- History is **per organization**, not lost when the tab closes.  
- Auto mode still goes through the same policy as an explicit pin.

---

## 5. Models (catalog)

**Route:** `/models`

```text
┌─ Models ───────────────────────────────────────────────────────────────┐
│  Everything this workspace is allowed to use.                          │
│                                                                        │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐      │
│  │ Janus Mock Small │  │ Mock Reasoning   │  │ Mock Embed       │      │
│  │ janus/mock-small │  │ …                │  │ …                │      │
│  │ [local] [verified] [cost]              │                      │      │
│  │ streaming · …    │  │                  │  │                  │      │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘      │
└────────────────────────────────────────────────────────────────────────┘
```

### Actions

| Action | Result |
|--------|--------|
| **Browse cards** | Only models eligible under current org mode/policy |
| **Open a card** | Model detail page |

---

## 6. Model detail

**Route:** `/models/[...id]` e.g. `/models/janus/mock-small`

```text
┌─ ← All models ─────────────────────────────────────────────────────────┐
│  Janus Mock Small                                                      │
│  janus/mock-small                                                      │
│  [tier] [privacy] [provider] [verified]                                │
│                                                                        │
│  Context ……    Max output ……    Cost ……    Latency ……                  │
│  Languages ……  Capabilities ……                                         │
│                                                                        │
│  Deployments                                                           │
│  · mock-small-local   [local] [ready] [cpu]                            │
│                                                                        │
│  [ Chat with this model ]                                              │
└────────────────────────────────────────────────────────────────────────┘
```

### Actions

| Action | Result |
|--------|--------|
| **Back to catalog** | `/models` |
| **Inspect deployments** | Key, privacy, availability, accelerator — **never** internal endpoints |
| **Chat with this model** | Jumps to Chat with that model pre-selected |

**Talking point:** Same eligibility rules as routing — the catalog cannot advertise a model the org cannot use.

---

## 7. Knowledge

**Route:** `/knowledge`

```text
┌─ Knowledge ──────────────────────────────┬─ Ingest / Search ───────────┐
│  New knowledge base                      │  Title                      │
│  Name [ Company handbook    ]            │  Content (textarea)         │
│  [ Create ]                              │  [ Ingest ]                 │
│                                          │                             │
│  · Company handbook   [1 docs]           │  Query [ gateway     ]      │
│    janus/mock-embed                      │  [ Search ]                 │
│                                          │                             │
│                                          │  [0.91] Every model call…   │
│                                          │  [0.72] Agents never…       │
└──────────────────────────────────────────┴─────────────────────────────┘
```

### Actions

| Action | Result |
|--------|--------|
| **Create knowledge base** | Named KB; embedding model pinned (default `janus/mock-embed`) |
| **Select a KB** | Target for ingest/search |
| **Ingest text** | Chunk → embed via gateway → store in pgvector; duplicate content rejected |
| **Search** | Ranked chunks with similarity scores |

**Talking points**

- Grounding for agents; citations come from these chunks.  
- Org-isolated (RLS). Text path today (PDF parsing is roadmap).

---

## 8. Agents

**Route:** `/agents`

```text
┌─ Create ─────────────────────┬─ Your agents / Run ─────────────────────┐
│  Name                        │  · Research assistant  [draft]          │
│  Slug                        │  · Support bot         [published]      │
│  Instructions                │                                         │
│  Knowledge base [ ▾ ]        │  [ Publish ]                            │
│  [ Create draft ]            │                                         │
│                              │  Run input                              │
│                              │  ┌─────────────────────────────┐        │
│                              │  │ When does the office open?  │        │
│                              │  └─────────────────────────────┘        │
│                              │  [ Run ]                                │
│                              │                                         │
│                              │  [completed] [2 steps]                  │
│                              │  Answer text…                           │
│                              │  Citations: “opens at 09:00 UTC…”       │
└──────────────────────────────┴─────────────────────────────────────────┘
```

### Actions

| Action | Result |
|--------|--------|
| **Create draft** | Versioned agent; optional KB + tools (`knowledge_search`, `clock`) |
| **Select agent** | Focus for publish/run |
| **Publish** | Marks version published (immutable publish semantics) |
| **Run** | Retrieve → tools → compose via **gateway**; shows status, steps, output, citations |

**Talking points**

- Agents never call a provider SDK; every model step hits the Model Gateway.  
- Citations prove grounding when knowledge is attached.

---

## 9. Usage & deployments

**Route:** `/usage`

```text
┌─ Usage & deployments ──────────────────────────────────────────────────┐
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                       │
│  │ Requests│ │ In toks │ │ Out toks│ │ Cost USD│                       │
│  │   11    │ │   …     │ │   …     │ │  0.00   │                       │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘                       │
│                                                                        │
│  Deployments                                                           │
│  Model              Key                 Privacy  Availability  Accel   │
│  janus/mock-small   mock-small-local    local    ready         cpu     │
│  …                                                                     │
└────────────────────────────────────────────────────────────────────────┘
```

### Actions

| Action | Result |
|--------|--------|
| **View usage** | Org-level totals from telemetry |
| **View deployments** | Health/privacy/accelerator the org can see — no credentials |

**Talking point:** Operators and buyers get transparency without exposing infrastructure endpoints.

---

## 10. Action map (all user intents)

```mermaid
flowchart TB
  Start([Open Janus]) --> Auth{Signed in?}
  Auth -- no --> Reg[Register / Sign in]
  Reg --> Home[Chat]

  Auth -- yes --> Home
  Home --> New[New conversation]
  Home --> Send[Send message / Stop]
  Home --> Pin[Pin model or Auto]

  Home --> Models[Browse models]
  Models --> Detail[Model detail]
  Detail --> ChatPin[Chat with this model]

  Home --> KB[Knowledge]
  KB --> CreateKB[Create base]
  KB --> Ingest[Ingest text]
  KB --> Search[Search chunks]

  Home --> Ag[Agents]
  Ag --> CreateAg[Create draft]
  Ag --> Pub[Publish]
  Ag --> Run[Run + read citations]

  Home --> Use[Usage]
  Use --> Totals[Token / cost totals]
  Use --> Deps[Deployment health]

  Home --> Out[Sign out]
```

---

## 11. Visual language (for designers / screenshots)

| Element | As built |
|---------|----------|
| Theme | Dark-first CSS variables; respects light preference |
| Brand | “J” mark + Janus wordmark in top bar |
| Density | Chat-first, not a multi-widget dashboard on first paint |
| Trust cues | Mode badge, privacy badges, verified metadata, routing explanation |
| What you never show | Provider keys, internal URLs, chain-of-thought |

Screenshot tips for Marketplace / sales decks: register → one Auto chat with attribution visible → model card → knowledge search hit → agent run with citation → usage table.

---

## 12. Honest scope for the deck

**In the UI today:** auth, chat + history, catalog/detail, agents, knowledge (text), usage/deployments.  

**Not in the UI yet (do not show as screens):** SSO login, PDF drag-drop, admin SSO/SCIM, full audit export console, GPU fleet console, Marketplace subscribe button inside the app.

Those belong in roadmap / services slides — not this UI tour.
