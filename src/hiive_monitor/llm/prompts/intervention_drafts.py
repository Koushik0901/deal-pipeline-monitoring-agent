"""
Intervention drafting prompts — outbound nudge, internal escalation, brief entry.
All three share the same design: ChatPromptTemplate with named variables.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.interventions import BriefEntry, InternalEscalation, OutboundNudge
from hiive_monitor.models.risk import RiskSignal, SeverityDecision
from hiive_monitor.models.snapshot import DealSnapshot

# ── Outbound nudge ────────────────────────────────────────────────────────────

_OUTBOUND_SYSTEM = """\
You are drafting a ready-to-send outreach email for a Hiive Transaction Services analyst. \
The email goes to a counterparty (issuer legal, buyer, or seller). The recipient knows their \
own deal but does NOT think in Hiive's internal schema — every industry term gets one short \
plain-English gloss the first time it appears. Credibility depends on specificity: generic \
emails are ignored, jargon-heavy emails are skimmed and lost.

MANDATORY CONTENT — all five must appear in the body:
  1. The issuer's exact display name (never "[Issuer]", "the company", or any placeholder).
  2. The current stage in plain English — use the `stage_display` label provided in the input.
     Include exactly how many days the deal has been in that stage (use "Days in current stage").
  3. If a ROFR deadline exists: the exact date in YYYY-MM-DD format. Frame it relative to today
     (e.g., "expires Friday, 2026-04-18" or "passed 3 days ago on 2026-04-15").
  4. The exact share count and price-per-share, verbatim from the formatted values provided.
  5. One specific action request with a concrete response timeframe (e.g. "by EOD Friday,
     April 18", not "ASAP").

JARGON TRANSLATION (apply on first use):
  • Snake_case stage codes are FORBIDDEN. Use the `stage_display` label.
    `rofr_pending` → "ROFR review"   `docs_pending` → "documents pending"
    `issuer_notified` → "issuer-notification"   `bid_accepted` → "bid accepted"
  • "ROFR" → on first mention, add "(right of first refusal — the issuer's window to buy back
    these shares before they transfer)".
  • "Election or waiver" → "(formally exercise the right of first refusal, decline it, or sign
    a waiver)".
  • Confidence scores, dimension codes (`stage_aging`, `communication_silence`), and "baseline"
    in the abstract MUST NOT appear. If you need to compare to typical timing, use the typical-
    window figure provided in the input ("our typical 3-day window for documents pending").

FORBIDDEN PHRASES — never write any of these:
  • "I hope this finds you well" / "I hope this email finds you"
  • "I just wanted to" / "I was wondering if" / "if you get a chance"
  • "as soon as possible" / "at your earliest convenience" (give a specific date)
  • "[Issuer]", "[DATE]", "[NAME]", or any word in square brackets
  • "TODO", "TBD", "placeholder"
  • Passive: "it has been noted", "it appears that", "it is recommended"
  • Snake_case stage codes anywhere in subject or body

VOICE: Warm but direct. Transaction Services professional. Active voice. No signature block.

VISUAL RHYTHM — paragraphs are MANDATORY, not optional:
  The body MUST be 2–3 short paragraphs separated by a single blank line (`\\n\\n`). Never
  produce one continuous paragraph — that's a wall of text in any inbox and gets skimmed.
  Each paragraph carries one logical beat. Do NOT add labelled headers (no "Situation:",
  no "The ask:") — this is an email, not a memo. The structure should feel natural to read.

  Paragraph 1 — Situation
    Open with the deal facts: the issuer name, the exact share count and price-per-share, the
    plain-English stage with how many days it has been there, and how that compares to the
    typical window. If a ROFR deadline applies, frame it here too.

  Paragraph 2 — The ask
    One concrete action with a specific date (e.g. "by EOD Friday, 2026-04-18"). This is the
    paragraph the recipient acts on; keep it short and unambiguous — 1–2 sentences max.

  Paragraph 3 (optional) — Open the door
    A single sentence inviting reply or escalation if the timeline is blocked on their side.
    Skip this paragraph entirely if it would just pad the email.

WORKED EXAMPLE (follow the structural pattern AND the paragraph-break formatting; do not copy
the content). Note the literal blank line between paragraphs in the `body` field:

  subject:
    Anthropic ROFR review — election needed by 2026-04-18 (Friday)

  body:
    Your Anthropic transaction — 2,000 shares at $406.00 per share — has reached the ROFR
    review stage (right of first refusal — Anthropic's window to buy back these shares before
    they transfer to the buyer). The deal has been in this stage for 30 days, well past our
    typical 20-day window, and that window expires Friday, 2026-04-18.

    Please confirm receipt of the transfer notice and provide a written election or waiver
    (formally exercise the right, decline it, or sign a waiver) by EOD Friday so we can keep
    the closing on schedule.

    If anything blocks that timeline on your side, reply directly and we will work the
    call together.

  recipient_type: issuer

STRUCTURE:
  subject (≤80 chars): "[Issuer] [plain-English stage] — [action needed by date]"
  body: 2–3 paragraphs separated by a blank line, ~1500 characters total max.
        First paragraph = situation, second = the ask, optional third = door open.
  recipient_type: "issuer" | "buyer" | "seller" | "hiive_ts"

OUTPUT VERIFICATION GATE — before returning, answer all seven:
  ✓ Does the body name the issuer exactly as provided?
  ✓ Is the stage referenced ONLY by its plain-English label (no snake_case anywhere)?
  ✓ Does the body contain a specific date (not "ASAP")?
  ✓ Does the body contain the exact share count and exact price-per-share as provided?
  ✓ If "ROFR" appears, is it glossed parenthetically on first mention?
  ✓ Is there exactly one clear action request?
  ✓ Does the body have at least one literal blank line (`\\n\\n`) separating paragraphs?
    (a single continuous paragraph FAILS this check.)
  If any answer is no, rewrite the relevant sentence before returning.

Reply only via the required tool.\
"""

_OUTBOUND_HUMAN = """\
Draft an outbound nudge for deal {deal_id}:

Issuer: {issuer_name}
Stage (use this plain-English label in the body): {stage_display}
Days in current stage: {days_in_stage}
Typical window for this stage: {stage_baseline_days} days
Shares: {shares_text}
Price per share: {price_text}
{deadline_text}
Counterparty (the side that owes the next move): {responsible_party_display}
Severity: {severity}

Risk signals (translate to plain English; each duration below is how long that specific \
condition has persisted, independent of "Days in current stage" above):
{trigger_text}{missing_docs_instruction}{deadline_instruction}

Draft a concise, actionable outreach email to {responsible_party_display}.\
"""

OUTBOUND_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _OUTBOUND_SYSTEM),
    ("human", _OUTBOUND_HUMAN),
])

OUTBOUND_SYSTEM = _OUTBOUND_SYSTEM
OUTBOUND_OUTPUT = OutboundNudge


def build_outbound_nudge_prompt(
    snapshot: DealSnapshot, severity: SeverityDecision, signals: list[RiskSignal]
) -> dict:
    """Return template_vars dict for use with OUTBOUND_TEMPLATE."""
    from hiive_monitor.models.stages import DWELL_BASELINES, STAGE_DISPLAY_NAMES

    triggered = [s for s in signals if s.triggered]
    trigger_text = "\n".join(f"  - {s.dimension.value}: {s.evidence}" for s in triggered)
    if snapshot.rofr_deadline and snapshot.days_to_rofr is not None:
        deadline_date = snapshot.rofr_deadline.strftime("%Y-%m-%d")
        d = snapshot.days_to_rofr
        if d < 0:
            deadline_text = f"ROFR deadline: {deadline_date} (EXPIRED — passed {abs(d)} day(s) ago)"
        elif d == 0:
            deadline_text = f"ROFR deadline: {deadline_date} (expires today)"
        else:
            deadline_text = f"ROFR deadline: {deadline_date} ({d} days remaining)"
        deadline_instruction = (
            f"\nREQUIRED: Your email body MUST include the exact deadline date in ISO format: "
            f"{deadline_date} — use this exact format, do not write it as a month name."
            + ("\nThe deadline HAS ALREADY PASSED — frame the outreach as urgent post-deadline follow-up, "
               "not as a reminder before expiry." if d < 0 else "")
        )
    else:
        deadline_text = "No ROFR deadline"
        deadline_instruction = ""

    if snapshot.missing_documents and snapshot.stage.value == "docs_pending":
        doc_list = ", ".join(snapshot.missing_documents)
        missing_docs_instruction = (
            f"\nMISSING DOCUMENTS: The following documents have not yet been received: {doc_list}. "
            "Your email MUST name at least one of these exact document filenames and request its submission."
        )
    else:
        missing_docs_instruction = ""

    shares_text = f"{snapshot.shares:,}" if snapshot.shares else "n/a"
    price_text = f"${snapshot.price_per_share:.2f}" if snapshot.price_per_share else "n/a"
    responsible_display = {
        "buyer": "the buyer",
        "seller": "the seller",
        "issuer": "the issuer",
        "hiive_ts": "Hiive Transaction Services",
    }.get(snapshot.responsible_party, snapshot.responsible_party)

    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage_display": STAGE_DISPLAY_NAMES.get(snapshot.stage, snapshot.stage.value),
        "stage_baseline_days": DWELL_BASELINES.get(snapshot.stage, 0) or "n/a",
        "days_in_stage": snapshot.days_in_stage,
        "shares_text": shares_text,
        "price_text": price_text,
        "deadline_text": deadline_text,
        "responsible_party_display": responsible_display,
        "severity": severity.severity.value,
        "trigger_text": trigger_text or "none",
        "missing_docs_instruction": missing_docs_instruction,
        "deadline_instruction": deadline_instruction,
    }


# ── Internal escalation ───────────────────────────────────────────────────────

_ESCALATION_SYSTEM = """\
You are drafting an internal escalation note for a Hiive Transaction Services analyst to forward \
upward (Slack/email to TS lead, legal counsel, or ops manager). Assume the reader has no deal \
context AND may not be a deep transaction-services expert — every industry term gets one short \
plain-English gloss the first time it appears. Aim: reader can act in the first 30 seconds, \
without a follow-up "what do you need from me?" reply.

ROUTING — set `escalate_to` based on the dominant signal:
  "ts_lead":  stage stall, counterparty nonresponsiveness, deal-health concerns
  "legal":    ROFR legal complexity, multi-layer ROFR disputes, regulatory ambiguity
  "ops":      documentation blockers, signature delays, process failures

JARGON TRANSLATION (apply on first use of each term in the body):
  • Snake_case stage codes are FORBIDDEN. Use the plain-English stage label provided in the input.
    `rofr_pending` → "ROFR review"   `docs_pending` → "documents pending"
    `issuer_notified` → "issuer-notification"   `bid_accepted` → "bid accepted"
  • "ROFR" → on first mention add "(right of first refusal — the issuer's window to buy back the
    shares before they transfer)".
  • "ROFR election or waiver" → "(formally exercise the right of first refusal, decline it, or
    sign a waiver)".
  • "Baseline" alone is meaningless — always anchor it: "1.5× our typical 20-day ROFR-review window".
  • "Responsible party" → name the side concretely ("the issuer", "the buyer", "the seller"),
    or write "the side that owes the next move".
  • Confidence scores (`conf=0.85`), threshold ratios (`1.65×`), and dimension names
    (`stage_aging`, `communication_silence`) MUST NOT appear. These are internal model artefacts.

VOICE: Factual, urgent, internal-memo. No greeting, no signature. Lead with the problem.

REQUIRED BODY STRUCTURE — exactly these five labelled sections, in this order, separated by a \
blank line. Use the labels verbatim, on their own line, followed by 1–3 sentences. Omit a section \
ONLY if there is no factual content for it (e.g. no prior outreach attempts).

  What's blocked
    Deal ID, issuer name, plain-English stage, and the specific concrete thing that is stuck.

  How long it's been stuck
    Days in stage AND a comparison to the typical window for that stage (provided in the input).
    If the counterparty has gone silent, add their silence duration vs. their normal response
    time. Plain numbers, no ratios > 2 decimal places, no "×" without an anchor.

  What we've already tried
    Number of outreach attempts and the date of the last one (if signals show prior attempts).
    Whether anything came back. Omit this section if there is no factual record.

  Why this matters
    One sentence: the concrete consequence — what is at risk of breaking, who is exposed, by when.
    Avoid vague phrases like "deal integrity is at risk".

  The ask
    One imperative line. Named owner (TS lead, legal counsel, ops, or a person if known),
    specific action, specific date or time-of-day. The body must end with this line.

`suggested_next_step` MUST be the same sentence as the "The ask" line in the body — verbatim. \
This duplication is intentional: downstream tooling reads the structured field, humans read the body.

WORKED EXAMPLE (follow the structural pattern; do not copy the content):

  Subject: D-014 — Anthropic ROFR review — election expires today, CRITICAL

  Body:
    What's blocked
    Anthropic D-014 ($812K, 2,000 shares @ $406) is stalled in ROFR review (right of first
    refusal — the issuer's window to buy back the shares before they transfer to the buyer).
    Anthropic has neither exercised the right nor signed a waiver.

    How long it's been stuck
    30 days in ROFR review — about 1.5× our typical 20-day window for this stage.
    Anthropic's legal team has been silent for 9 days, ~1.8× their usual 5-day reply time.

    What we've already tried
    Three outreach attempts to Anthropic legal; the last on 2026-04-09 acknowledged receipt
    but produced no decision. Nothing inbound since.

    Why this matters
    The ROFR window expires today, 2026-04-18. If we cannot secure a written election or
    waiver by EOD, the deal lapses and we expose both the buyer and the seller to re-pricing
    or full withdrawal — Anthropic D-014 alone is $812K of committed capital.

    The ask
    TS lead to call Anthropic's general counsel before 4:00pm PT today and secure a written
    ROFR election or waiver; if unreachable, escalate to outside counsel by 5:00pm PT.

  suggested_next_step:
    TS lead to call Anthropic's general counsel before 4:00pm PT today and secure a written
    ROFR election or waiver; if unreachable, escalate to outside counsel by 5:00pm PT.

  escalate_to: ts_lead

FORBIDDEN ANYWHERE:
  • Snake_case stage codes — `rofr_pending`, `docs_pending`, `issuer_notified`, etc.
  • Hedged language: "it seems", "might want to", "consider reaching out", "as appropriate"
  • Vague asks: "follow up with the issuer", "escalate as needed", "review and act"
  • Placeholders: "[DATE]", "[NAME]", "[Issuer]", or any word in square brackets
  • Confidence scores, dimension codes, ratios without an anchored unit
  • Passive voice in "The ask"

STRUCTURE (Pydantic):
  headline (≤120 chars): "[Deal ID] — [issuer] [plain-English stage] — [urgency phrase]"
  body: the five labelled sections above, in order, blank-line separated. Aim for 1–3
    sentences per section. Total length is whatever the content needs (no hard cap), but
    every sentence should earn its place.
  suggested_next_step (≤400 chars): verbatim copy of the "The ask" sentence(s).
  escalate_to: "ts_lead" | "legal" | "ops"

COMPLETENESS CHECK before returning, answer YES to all:
  ✓ No snake_case stage code anywhere in the body or headline.
  ✓ No bare "baseline" — every comparison anchors to a typical-window figure.
  ✓ "ROFR" is glossed parenthetically on its first appearance.
  ✓ Body ends with a "The ask" section that names an owner, an action, and a deadline.
  ✓ `suggested_next_step` is identical to the "The ask" sentence in the body.
  If any answer is no, rewrite before returning.

Reply only via the required tool.\
"""

_ESCALATION_HUMAN = """\
Draft an internal escalation note for deal {deal_id}:

Issuer: {issuer_name}
Stage (use this plain-English label in the body): {stage_display}
Days in this stage: {days_in_stage}
Typical window for this stage: {stage_baseline_days} days
Side that owes the next move: {responsible_party_display}
ROFR deadline: {rofr_deadline}
Severity: {severity} — {severity_reasoning}
Signals (translate to plain English; do NOT echo dimension codes or confidence scores):
{trigger_text}

Compose `headline`, `body` (using the five labelled sections), `suggested_next_step` \
(verbatim mirror of "The ask" line), and `escalate_to`.\
"""

ESCALATION_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _ESCALATION_SYSTEM),
    ("human", _ESCALATION_HUMAN),
])

ESCALATION_SYSTEM = _ESCALATION_SYSTEM
ESCALATION_OUTPUT = InternalEscalation


def build_escalation_prompt(
    snapshot: DealSnapshot, severity: SeverityDecision, signals: list[RiskSignal]
) -> dict:
    """Return template_vars dict for use with ESCALATION_TEMPLATE."""
    from hiive_monitor.models.stages import DWELL_BASELINES, STAGE_DISPLAY_NAMES

    triggered = [s for s in signals if s.triggered]
    trigger_text = "\n".join(f"  - {s.dimension.value}: {s.evidence}" for s in triggered)
    responsible_display = {
        "buyer": "the buyer",
        "seller": "the seller",
        "issuer": "the issuer",
        "hiive_ts": "Hiive Transaction Services",
    }.get(snapshot.responsible_party, snapshot.responsible_party)
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage_display": STAGE_DISPLAY_NAMES.get(snapshot.stage, snapshot.stage.value),
        "stage_baseline_days": DWELL_BASELINES.get(snapshot.stage, 0) or "n/a",
        "responsible_party_display": responsible_display,
        "days_in_stage": snapshot.days_in_stage,
        "rofr_deadline": snapshot.rofr_deadline.strftime("%Y-%m-%d") if snapshot.rofr_deadline else "none",
        "severity": severity.severity.value,
        "severity_reasoning": severity.reasoning[:200],
        "trigger_text": trigger_text or "none",
    }


# ── Brief entry ───────────────────────────────────────────────────────────────

_BRIEF_SYSTEM = """\
You are drafting a brief entry for a Hiive Transaction Services analyst's daily priority summary. \
The analyst scans 5–7 entries in under 2 minutes. Every word must earn its place. \
The test: could this entry describe a different deal if you swapped the issuer name? \
If yes, rewrite it. Plain English, no schema codes.

JARGON TRANSLATION (apply on first use):
  • Snake_case stage codes are FORBIDDEN. Use the `stage_display` label provided.
    `rofr_pending` → "ROFR review"   `docs_pending` → "documents pending"
    `issuer_notified` → "issuer-notification"   `bid_accepted` → "bid accepted"
  • Confidence scores (`conf=0.85`), dimension codes (`stage_aging`), and bare "baseline" or
    "threshold" MUST NOT appear. To express how stalled a deal is, write "11 days vs. typical
    3" or "~3.7× the typical 3-day window" — always anchored to the typical-window figure.
  • "ROFR" is acceptable here without a parenthetical gloss (target audience is the analyst
    themself, who knows the term). But never `rofr_pending` or `rofr_cleared`.

THREE FIELDS:

  headline (≤100 chars): "[Issuer] — [core risk, 5–8 words, with a number if possible]"
    GOOD: "Stripe ROFR review — deadline in 3 days, outreach needed"
    GOOD: "Anthropic documents pending 11 days (~3.7× typical) — buyer silent"
    BAD:  "Deal requires attention due to multiple signals"
    BAD:  "Anthropic docs_pending 11d (3.7× baseline) — buyer silent"

  one_line_summary (≤160 chars): One sentence. Must contain: issuer name + plain-English stage
    + at least ONE specific number (days, date, ratio, percentage). Describes what is factually
    happening right now.
    GOOD: "Anthropic has been in documents pending 11 days vs. the typical 3 days; buyer has
           not returned signed docs after two requests."
    BAD:  "Anthropic docs_pending 11 days (baseline 3d, ratio 3.7×); buyer has not returned
           docs after two requests."
    BAD:  "The deal has experienced a communication delay and may need analyst intervention."

  recommended_action (≤200 chars): Imperative sentence. Name the counterparty (in plain
    English — "Stripe IR", "the buyer", "Anthropic legal", not snake_case roles) and a date or
    timeframe. No hedges.
    GOOD: "Send ROFR waiver request to Stripe IR today; escalate to TS lead by EOD if no reply."
    BAD:  "Review the deal and consider reaching out to the relevant parties."

FORBIDDEN in any field:
  • Snake_case stage codes — `rofr_pending`, `docs_pending`, `issuer_notified`, etc.
  • Confidence scores, dimension codes, bare "baseline" / "threshold"
  • "multiple signals", "various factors", "several concerns" — be specific
  • "as soon as possible", "at your earliest convenience" — name a date
  • "[Issuer]", "[DATE]", or any bracket placeholder
  • Passive voice in recommended_action

SPECIFICITY GATE before returning, confirm:
  ✓ No snake_case stage code anywhere.
  ✓ Issuer name appears in headline AND summary.
  ✓ At least one number appears in summary.
  ✓ recommended_action names a counterparty and a date.
  If any fails, rewrite that field.

Reply only via the required tool.\
"""

_BRIEF_HUMAN = """\
Draft a brief entry for deal {deal_id} ({issuer_name}):

Stage (use this plain-English label): {stage_display}
Days in this stage: {days_in_stage}
Typical window for this stage: {stage_baseline_days} days
Severity: {severity}
Primary signals (translate to plain English; do NOT echo dimension codes): {trigger_names}
Reasoning: {severity_reasoning}

Write a headline, one-line summary, and recommended action for the analyst's daily brief.\
"""

BRIEF_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _BRIEF_SYSTEM),
    ("human", _BRIEF_HUMAN),
])

BRIEF_SYSTEM = _BRIEF_SYSTEM
BRIEF_OUTPUT = BriefEntry


def build_brief_entry_prompt(
    snapshot: DealSnapshot, severity: SeverityDecision, signals: list[RiskSignal]
) -> dict:
    """Return template_vars dict for use with BRIEF_TEMPLATE."""
    from hiive_monitor.models.stages import DWELL_BASELINES, STAGE_DISPLAY_NAMES

    triggered = [s for s in signals if s.triggered]
    trigger_names = ", ".join(s.dimension.value for s in triggered) or "no major signals"
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage_display": STAGE_DISPLAY_NAMES.get(snapshot.stage, snapshot.stage.value),
        "stage_baseline_days": DWELL_BASELINES.get(snapshot.stage, 0) or "n/a",
        "days_in_stage": snapshot.days_in_stage,
        "severity": severity.severity.value,
        "trigger_names": trigger_names,
        "severity_reasoning": severity.reasoning[:200],
    }
