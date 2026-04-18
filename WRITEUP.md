## What problem did you choose and why?

Hiive's Transaction Services analysts track 30–60 live deals simultaneously across the deal lifecycle, each carrying its own ROFR window, document dependencies, and counterparty timelines. No analyst holds that full state in working memory. Deals that break late usually break because attention slipped on one of them for a week, not because they were unsalvageable. This is an attention problem, not a modeling problem, the right shape for an agent that amplifies analyst attention rather than replacing analyst judgment. TS is also a regulated workflow: every flag must carry visible reasoning, and no message leaves without human approval. Those constraints rule out autonomous-agent designs from the start.

## What you built

A Daily Brief: what an analyst opens at 9 am. It surfaces the 5–7 deals needing action today, ranked by severity, each with a drafted intervention (outbound nudge, internal escalation, or watchlist note) and the full reasoning behind the flag. Approve, edit, or dismiss in place; nothing sends autonomously.

Two graphs do the work, and the distinction matters: the Monitor is deliberately not agentic; the Investigator deliberately is. The Monitor screens every live deal cheaply on a fixed per-tick budget. The Investigator runs only on the queued top-N and has a variable execution path; it can call enrichment tools (communication content, prior observations, issuer history, prior intervention outcomes) when signals are ambiguous, capped at two rounds. Loop closure is event-driven: approved interventions suppress re-flagging on the same deal for three ticks, so the agent doesn't pester analysts about deals they've already handled.

## How it works

One tick: cheap Gemma4 screening scores all live deals; suppression filters out deals recently acted on; the top-N by attention enter the Investigator queue. For each, the Investigator evaluates six risk dimensions: five in one combined Sonnet 4.6 call (stage aging, deadline proximity, communication silence, missing prerequisites, unusual characteristics) and a sixth (counterparty non-responsiveness) computed deterministically because it's pure math over inbound timestamps, not LLM-shaped work. The Investigator then asks itself whether the signal picture is sufficient or needs enrichment; if ambiguous, it calls one tool and re-asks, capped at two rounds. Severity scores against a four-level rubric; act or escalate triggers a draft intervention. Observations and interventions persist with reasoning attached, queryable per tick.

## Reflection
AI was at its best when I gave it a spec instead of a prompt. I used Spec Kit with Claude Code to write the contracts and 39 YAML eval fixtures first, then let it fill in prompts and graph code against them. Working agent loop in hours, and every prompt change after that got measured against the same scenarios instead of my gut.

Where it fell short wasn't hallucination; it was silent integration bugs I didn't think to look for. The one that bit me hardest: a severity output where the verdict field said ESCALATE, but the reasoning paragraph concluded ACT. Both fields validated. No test caught it. I only noticed because I was clicking through the UI looking at something else, and once I started looking, I found a few more like it.

I could build faster than I could verify. Evals only catch what you've thought to specify, and the things you haven't thought to specify are what actually break. Every bug I found by hand became a fixture.