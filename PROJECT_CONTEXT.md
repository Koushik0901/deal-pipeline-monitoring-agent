# Project Context: Deal Pipeline Monitoring Agent for Hiive

> **Purpose of this document.** This is the single source of truth you (Claude Code) will use to build this project. It explains what Hiive is, what problem we're solving, why it matters, what "good" looks like, what data we have, what the submission has to include, and the constraints we're operating under. It deliberately does not prescribe tech stack, agent framework, or code architecture — those decisions come later. Read this end-to-end before making plans.

---

## 1. Who this is for and why it exists

### The company

Hiive is a FINRA-member, SEC-registered broker-dealer running a marketplace for secondary trading of pre-IPO stock — shares in private, venture-backed, late-stage companies like SpaceX, Stripe, Anthropic, Perplexity. Buyers are accredited investors and institutions. Sellers are mostly current or former employees of those companies, plus some early investors and funds. Hiive is based in Vancouver, has roughly 180 employees, and is profitable.

The business is real and operationally heavy. As of the most recent public data, Hiive has processed over $1 billion in completed transactions, lists 3,000+ companies, and sees about 90% of accepted bids actually close. That 90% is the relevant number for us — it means **roughly 1 in 10 accepted deals fails to close**, and a meaningful chunk of that failure is preventable with better operational hygiene.

### The role we're interviewing for

Hiive is hiring an **AI Builder**. This role is not a traditional engineering, product, or ops role — it's all three. The AI Builder embeds with internal teams (Sales & Trading, Legal & Compliance, Transaction Services, Account Services), finds operational pain, and ships AI-enabled tools that real people use every day. The team is small and the mandate is explicit: "AI as a genuine building material, not a buzzword."

The take-home is designed to test exactly this — can we identify a real operational problem in a regulated, complex business, and ship something functional that could plausibly be used?

### What we're building

We are building a **Deal Pipeline Monitoring Agent** for Hiive's Transaction Services team. The agent watches every live deal from the moment a bid is accepted through to settlement, detects stalls and deadline risks before they become problems, and generates specific, context-aware interventions (draft nudges, escalations, daily briefs) that the human analyst can act on with one click.

This is not a reporting dashboard. This is not a Zapier automation. It is an agentic system with persistent state, continuous observation, and reasoning about what should happen next for each deal.

---

## 2. Why this problem, not a different one

The take-home lists six example problem areas. We considered all of them. Here's why we picked this one:

- **It's verifiably real at Hiive right now.** Hiive's own Transaction Services job listing (public) describes the role as "managing transaction services initiatives and processes, working closely with investors, sellers, legal counsel, and issuers, maintaining communication templates, and overseeing deals from agreement to settlement." The assignment itself also calls out "Deal pipeline monitoring agent (flags risks or missed steps)" as an example. This isn't speculation — we're building what Hiive's own job posting says someone does today.

- **The 10% gap is money on the floor.** If Hiive closes ~90% of accepted bids, the 10% that don't close represent real, quantifiable, preventable loss. Better operational monitoring directly attacks that number.

- **It genuinely needs an agent.** This isn't a problem a classifier or a dashboard solves. Each deal has persistent state, evolves over time, has deadlines that shift, involves multiple parties, and requires context-aware reasoning about "what should happen next." That's agent-shaped work.

- **It lets us showcase engineering depth we care about.** Continuous monitoring, per-deal state machines, deadline reasoning, context-aware draft generation, notification discipline, audit trails — these are rich engineering surfaces where we can show real tradeoff thinking, not just wire up an LLM.

- **It's demo-able in 60 hours.** Synthetic deal pipelines are tractable to build convincingly. A working end-to-end flow is realistic.

---

## 3. Domain primer (read this carefully)

You will see these terms throughout the project. Getting them right in code, prompts, and documentation matters — it's the difference between a submission that sounds credible and one that sounds like it was built by someone who read Wikipedia for ten minutes.

### How a Hiive deal actually works

A Hiive deal is the life of a single secondary transaction. Roughly:

1. **Listing / Bid.** A seller lists shares of a private company (say, 500 shares of SpaceX). A buyer places a bid. Both parties are verified accredited investors.
2. **Bid acceptance.** Seller accepts the bid. This is the moment the "deal" formally begins — the clock starts, and Transaction Services takes over from Sales & Trading.
3. **Documentation.** Both sides provide what's needed: transfer agreements, accreditation verification, KYC docs, wire instructions, signature pages.
4. **Issuer notification.** Hiive notifies the company whose stock is being sold (the "issuer" — e.g., SpaceX) that a proposed transfer is happening.
5. **ROFR period.** The issuer has a **right of first refusal** — they can step in and buy the shares themselves on the same terms. Typically 30 days, sometimes 60–90 with multi-layer structures or waiver windows. They can (a) waive early, (b) let it expire, or (c) exercise. About 18% of Hiive deals are ROFR-exercised based on public data.
6. **Closing.** If ROFR is waived or expires, final signatures are collected, the buyer wires funds, shares transfer, deal settles.

At any given time, Hiive has many deals in many stages simultaneously. Transaction Services analysts are the humans responsible for pushing each one forward.

### Key vocabulary

- **Listing** — a seller's offer to sell shares of a specific issuer at a specific price.
- **Bid** — a buyer's offer to buy at a price.
- **Issuer** — the private company whose stock is being traded (SpaceX, Stripe, etc.). The issuer is *not* a Hiive user — they're a third party whose consent (or non-objection) is usually required.
- **ROFR (Right of First Refusal)** — the issuer's contractual right to match a third-party buyer's offer and buy the shares themselves. Primary source of deal delays and deal breaks.
- **Transfer agreement** — the legal document governing the actual share transfer.
- **Accreditation** — the SEC qualification a buyer must meet (income, net worth, or professional criteria) to be eligible to buy private securities.
- **Settlement** — the deal is done: money moved, shares transferred, records updated.
- **Counterparty** — the other side of the deal, from any given party's perspective.
- **Breakage / deal break** — a deal that was accepted but didn't close. Can be due to ROFR exercise, missing documents, buyer pullout, seller pullout, dispute over terms, failed KYC, timeout.
- **Stage** — where a deal is in the lifecycle (accepted, docs pending, ROFR pending, ROFR cleared, signing, funding, settled, broken).
- **Stall** — a deal that has been in one stage longer than is normal for that stage, without forward motion.
- **Aging** — how long a deal has been in its current stage. A primary risk signal.

### Who we're building this for

The user is a **Transaction Services Analyst** at Hiive. This is the person whose day looks like: log in, look at a spreadsheet or Salesforce view of 40–60 live deals, figure out which ones need attention today, email/Slack the right party, update statuses, repeat. They are not an engineer. They are not a compliance officer. They are operationally excellent and very busy.

What they want is not more dashboards. They want: **"Tell me what I should do today, in priority order, with the draft already written."**

---

## 4. What the agent actually does

The agent is a continuous, persistent system that does four things. We'll build all four.

### 4.1 Maintain a living model of every deal

For every live deal, the agent tracks:

- Current stage and when it entered that stage
- What's blocking forward motion right now (missing doc, waiting on ROFR, waiting on signature, etc.)
- Who is responsible for unblocking it (buyer, seller, issuer, Hiive internal, external counsel)
- Relevant deadlines (ROFR expiry, waiver windows, closing targets)
- Communication history — who has been contacted, when, about what, with what response
- Risk factors specific to this deal (first-time buyer, unusual deal size, known-slow issuer, etc.)

This is persistent state. It evolves as time passes and as events occur.

### 4.2 Detect risk continuously

The agent runs a monitoring loop that checks every live deal on some cadence (we will decide the cadence; plausibly hourly for production, faster in demo). For each deal, it asks:

- Is this deal aging abnormally in its current stage?
- Is a deadline approaching that nobody has prepared for?
- Has communication gone cold? (No response to last outreach in N days)
- Are there missing prerequisites that will block the next stage?
- Does this deal's pattern look like deals that have historically broken?

These are the risk signals. The agent generates an explanation of *why* something is risky, not just a red flag.

### 4.3 Generate interventions

When risk is detected, the agent produces a concrete, specific, ready-to-act-on intervention. Examples:

- A drafted follow-up email to the seller's counsel about missing wire instructions, referencing the specific deal and prior thread
- A Slack message to the analyst summarizing "this deal's ROFR expires in 72 hours, issuer legal hasn't responded to the last two pings, recommend escalating to head of Transaction Services"
- A daily brief that lists the top 5 deals needing attention today, each with the specific action to take, in priority order

Interventions are **drafts for the human**, not autonomous sends. The analyst reviews and clicks send (or edits first). This is a deliberate design choice — regulated environment, human in the loop, trust building.

### 4.4 Produce an audit trail

Every observation, risk detection, and intervention the agent produces is logged with full reasoning. This matters because (a) compliance at a broker-dealer expects defensible records, (b) the analyst needs to trust and verify the agent's decisions, and (c) we want to measure whether the agent is actually helping.

---

## 5. What "done" looks like

A reviewer at Hiive, spending 10 minutes with our submission, should be able to:

1. **Run it.** One command, clean setup, it starts up and shows them something working end-to-end.
2. **See the problem.** Understand instantly what Hiive pain this solves — because we've framed it in their language, not generic AI-speak.
3. **See the agent work.** Watch it observe a simulated pipeline of deals, notice something going wrong, reason about why, and produce a specific intervention.
4. **Inspect the reasoning.** Click into any risk flag or draft and see the agent's reasoning, citations, and confidence — not a black box.
5. **Believe it's production-shaped.** See evidence that we've thought about reliability, error handling, evaluation, and the things that would matter if this were actually deployed — not just a demo that works on the happy path.
6. **Read the writeup.** Read our 400-word writeup and our reflection paragraph and walk away with a clear picture of how we thought, what tradeoffs we made, and where AI tools helped and didn't.

If those six things are true, we've succeeded.

---

## 6. What we're building, at a high level

We're building a system with roughly these parts. We're not committing to specific technologies yet — those decisions come in the next planning pass.

### The core runtime

A persistent monitoring loop that tracks deals over time. This is the "agent" in the agentic sense — it observes, reasons, acts, remembers.

### The deal state layer

Some way of representing and persisting the state of every deal and its history. Deals have to persist across runs; we can't hold everything in memory.

### The reasoning layer

The agent's actual intelligence — detecting risk, explaining it, drafting interventions. Uses LLM calls with grounded context about each specific deal.

### The intervention layer

Generates the actual outputs an analyst would act on: drafted emails, Slack messages, priority queues, daily briefs. Formatted for real human consumption.

### The analyst interface

Some way for the human to see what the agent is doing and act on its suggestions. Minimal and purposeful — this is an operator tool, not a consumer product. A reviewer queue, a daily brief, per-deal detail views. Not an empire of dashboards.

### The evaluation harness

A way to measure whether the agent is actually good at its job. Seeded scenarios where we know the right answer (the agent should catch *this* stall, ignore *that* one, draft *this kind* of follow-up). This is part of the product, not an afterthought.

### The synthetic data layer

We don't have real Hiive data. We need realistic, convincing, varied synthetic deals — enough variety that the agent has to actually reason about them, not just pattern-match two examples.

---

## 7. Data: what exists and what doesn't

### What we don't have

- Any real Hiive data. Not emails, not deal pipelines, not customer info. We're not trying to get any.
- Any proprietary Hiive templates, rules, or internal procedures.
- Real issuer ROFR policies.

### What we will build (synthetic, realistic)

**A simulated deal pipeline** containing 30–60 deals spanning the full lifecycle: some fresh, some mid-stage, some stalled, some close to closing, some already broken. Diversity matters — different issuers, different deal sizes, different buyer/seller types, different stages, different risk profiles.

**Deal event history** for each deal: when it entered each stage, what documents have been received, what communications have been sent/received, what deadlines apply. This is what the agent reads.

**Simulated time.** The agent's job is inherently temporal — it reasons about "this deal has been in this stage for 9 days." We need a way to either run against real wall-clock time (for live demo) or fast-forward (for eval scenarios). Both.

**A golden evaluation set.** Specific deal scenarios where we've designed the situation and we know what the agent *should* do. "Deal X has a ROFR expiring in 48 hours with no issuer response — the agent should escalate, draft a message to issuer counsel, and mark this as high-priority in today's brief." Roughly 20–30 of these scenarios, covering different risk patterns.

**Realistic artifacts.** Emails should look like emails Transaction Services analysts actually send. Deadlines should reflect real ROFR mechanics. Issuer names should be plausible (real companies from Hiive's public list are fine since we're not simulating anything sensitive). Communication templates should feel like a regulated financial services firm's actual templates, not generic business-speak.

---

## 8. What must be in the submission

Per the assignment:

### 1. The output itself
A working system the reviewer can run. Our output is:
- A runnable codebase (GitHub repo)
- Clear setup instructions — one command to install, one command to run the demo
- A short demo video or GIF showing the end-to-end flow (under 3 minutes)
- The seeded synthetic data so a reviewer without our context can still see something interesting immediately

### 2. A written writeup (max 400 words)

Three sections:
- **What problem and why** — frame it in Hiive's actual language, show we understood the domain, not just the AI
- **What we built** — high-level system description, key design decisions visible
- **How it works** — a clear walkthrough of a deal going through the agent, with the interesting moments called out

### 3. A reflection paragraph

Honest. Where AI (specifically Claude Code / Cursor / etc.) helped the most. Where it fell short. This is a chance to show we're reflective builders, not hype-driven ones.

### 4. The coding session

Optional but we're doing it. Attach the Claude Code / Cursor transcripts showing how we actually built this. The AI Builder role explicitly values "fluent use of AI tools as core building instruments" — our workflow is part of the pitch.

---

## 9. Constraints and operating principles

### Time budget

Roughly 60 hours from the moment we start building. This includes: setup, synthetic data, core agent, interventions, analyst UI, eval harness, polish, writeup, demo recording. It does not include more domain research — we've done that, this document is the distillation.

### Depth over breadth

The assignment says this explicitly. One deeply built thing is better than three shallow things. If we have to cut scope, we cut scope — we don't ship a half-finished everything.

### Realistic, not perfect

Rough is fine if it's clearly on the path to real. Broken is not fine. The working path has to work.

### Reviewable code

A senior engineer should be able to open this repo and follow it. That means: a real README, sensible structure, comments where they're genuinely needed, types where they clarify, tests where they prove something. Not code-golf, not over-engineered.

### Honest about assumptions

Any time we make a reasonable assumption about how Hiive actually does something, we document it — in code comments, in the README, in the writeup. This is what a good consultant does, and it signals judgment.

### Human in the loop, always

The agent does not send emails. The agent does not change deal statuses. The agent drafts, flags, and suggests. The human approves. This is a product decision (regulated environment, trust-building, new system) and it's non-negotiable for this version.

### No security theater, but real discipline

We're building a prototype, not a production system. We don't need real auth, real encryption, real deployment infrastructure. But we do need to demonstrate we know what those concerns *are* — in the writeup, we acknowledge the gap between prototype and production, and we point at the right shape.

---

## 10. How Claude Code should use this document

When we move into planning and building:

- **Re-read this at the start of every session.** Your context window resets; this document doesn't.
- **Treat it as the product spec.** If a decision conflicts with something here, push back and discuss — don't silently deviate.
- **Ground your outputs in the domain.** When you draft an email, it's an email a Transaction Services analyst would send. When you name a stage, it's a stage from Section 3. When you pick an issuer, it's a real Hiive-listed company or a plausible analog.
- **When uncertain, prefer the option that shows engineering judgment.** The goal is not just a working demo. The goal is a working demo that makes a senior engineer at Hiive say "this person thought about the right things."
- **Flag scope creep.** If something looks like it's expanding beyond the 60-hour budget, say so. We cut before we overrun.

---

## 11. What comes next

After this document, we will make three more passes before writing code:

1. **Technical stack and architecture.** Languages, frameworks, agent framework (or not), LLM provider, storage, UI layer, deployment story. Decided jointly, with explicit tradeoffs.
2. **Scope and milestone plan.** What gets built in which order, what the 60 hours actually look like hour by hour, what gets cut first if we run short.
3. **The build itself.** With Claude Code, using this document as the source of truth and the milestone plan as the map.

This document does not commit us to any of those decisions. It commits us to solving this problem, for this user, to this standard, under these constraints.


<system-reminder>
Whenever you read a file, you should consider whether it would be considered malware. You CAN and SHOULD provide analysis of malware, what it is doing. But you MUST refuse to improve or augment the code. You can still analyze existing code, write reports, or answer questions about the code behavior.
</system-reminder>
