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
This email goes to a counterparty (issuer legal, buyer, or seller) and will be sent with \
minimal editing. Credibility depends on specificity — generic emails are ignored.

MANDATORY CONTENT — all five MUST appear verbatim in the body:
  1. The issuer's exact display name (never "[Issuer]", "the company", or any placeholder)
  2. The current deal stage and exactly how many days the deal has been in that stage — use the
     "Days in current stage" figure provided; do NOT substitute a duration from the risk signals
  3. If a ROFR deadline exists: the exact date in YYYY-MM-DD format (no month-name alternatives)
  4. The exact share count and price-per-share from the deal facts. Use the formatted values
     provided ("Shares" and "Price per share") verbatim — do not round, paraphrase, or omit.
  5. One specific action request with a concrete response timeframe (e.g. "by EOD Friday")

FORBIDDEN PHRASES — your output must contain none of these:
  • "I hope this finds you well" / "I hope this email finds you"
  • "I just wanted to" / "I was wondering if" / "if you get a chance"
  • "as soon as possible" / "at your earliest convenience" (replace with a specific date)
  • "[Issuer]", "[DATE]", "[NAME]", or any word in square brackets
  • "TODO", "TBD", "placeholder"
  • Passive constructions: "it has been noted", "it appears that"

VOICE: Warm but direct. Transaction Services professional. Active voice. No signature block.

STRUCTURE:
  subject: One line, ≤80 chars. "[Issuer] [stage] — [action needed]"
  body: 3–5 sentences. Under 1200 characters total.
  recipient_type: "issuer" | "buyer" | "seller" | "hiive_ts"

OUTPUT VERIFICATION GATE — before returning, answer all four:
  ✓ Does the body name the issuer exactly as provided?
  ✓ Does the body contain a specific date (not "ASAP")?
  ✓ Does the body contain the exact share count and exact price-per-share as provided?
  ✓ Is there exactly one clear action request?
  If any answer is no, rewrite the relevant sentence before returning.

Reply only via the required tool.\
"""

_OUTBOUND_HUMAN = """\
Draft an outbound nudge for deal {deal_id}:

Issuer: {issuer_name}
Stage: {stage}
Days in current stage: {days_in_stage}
Shares: {shares_text}
Price per share: {price_text}
{deadline_text}
Responsible party: {responsible_party}
Severity: {severity}

Risk signals (each duration below is how long that specific condition has persisted — \
independent of "Days in current stage" above):
{trigger_text}{missing_docs_instruction}{deadline_instruction}

Draft a concise, actionable outreach email to the {responsible_party}.\
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

    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "days_in_stage": snapshot.days_in_stage,
        "shares_text": shares_text,
        "price_text": price_text,
        "deadline_text": deadline_text,
        "responsible_party": snapshot.responsible_party,
        "severity": severity.severity.value,
        "trigger_text": trigger_text or "none",
        "missing_docs_instruction": missing_docs_instruction,
        "deadline_instruction": deadline_instruction,
    }


# ── Internal escalation ───────────────────────────────────────────────────────

_ESCALATION_SYSTEM = """\
You are drafting an internal escalation note for a Hiive Transaction Services analyst. \
The reader (TS lead, legal counsel, or ops manager) has no deal context — give them \
everything they need to take action in the first 30 seconds of reading.

ROUTING LOGIC — set escalate_to based on dominant signal:
  "ts_lead":  stage stall, counterparty nonresponsiveness, deal health concerns
  "legal":    ROFR legal complexity, multi-layer ROFR disputes, regulatory ambiguity
  "ops":      documentation blockers, signature delays, process failures

MANDATORY CONTENT (all four must appear):
  1. Deal ID and issuer name in the first sentence — never postpone this
  2. The specific blockage: what exactly is stuck and for how many days
  3. What outreach has already been sent (cite number of attempts if signals show it)
  4. One concrete suggested next step: named owner, specific action, specific date

FORBIDDEN:
  • Vague next steps: "follow up with the issuer", "reach out appropriately", "escalate as needed"
  • Hedged language: "it seems", "might want to", "consider reaching out"
  • Placeholders: "[DATE]", "[NAME]", any word in brackets
  • Passive voice in the suggested_next_step

VOICE: Factual, urgent, internal memo style. No greeting. Lead with the problem.

STRUCTURE:
  headline (≤100 chars): "[Deal ID] — [issuer] [what is blocked] — [urgency word]"
  body: 3–5 sentences, under 800 characters.
  suggested_next_step (≤200 chars): "[Owner] to [verb] [object] by [date/time]."
    Good: "TS lead to call Stripe IR team by 2026-04-18 and obtain ROFR election or waiver."
    Bad: "Follow up with the issuer as soon as possible."
  escalate_to: one of "ts_lead" | "legal" | "ops"

COMPLETENESS CHECK before returning: Does the body name the deal, the blockage duration, \
and prior attempts? Does suggested_next_step name an owner and a date? If not, rewrite.

Reply only via the required tool.\
"""

_ESCALATION_HUMAN = """\
Draft an internal escalation note for deal {deal_id}:

Issuer: {issuer_name}
Stage: {stage} ({days_in_stage} days)
ROFR deadline: {rofr_deadline}
Risk factors: {risk_factors}
Severity: {severity} — {severity_reasoning}
Signals: {trigger_text}

Who should this escalate to and what is the specific recommended next step?\
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
    triggered = [s for s in signals if s.triggered]
    trigger_text = "\n".join(f"  - {s.dimension.value}: {s.evidence}" for s in triggered)
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "days_in_stage": snapshot.days_in_stage,
        "rofr_deadline": snapshot.rofr_deadline.strftime("%Y-%m-%d") if snapshot.rofr_deadline else "none",
        "risk_factors": snapshot.risk_factors,
        "severity": severity.severity.value,
        "severity_reasoning": severity.reasoning[:200],
        "trigger_text": trigger_text or "none",
    }


# ── Brief entry ───────────────────────────────────────────────────────────────

_BRIEF_SYSTEM = """\
You are drafting a brief entry for a Hiive Transaction Services analyst's daily priority summary. \
Analysts scan 5–7 entries in under 2 minutes. Every word must earn its place. \
The test: could this entry describe a different deal if you swapped the issuer name? \
If yes, rewrite it.

THREE FIELDS:

  headline (≤100 chars): "[Issuer] — [core risk, 5–8 words, with a number if possible]"
    GOOD: "Stripe ROFR deadline in 3 days — outreach needed"
    GOOD: "Anthropic docs_pending 11d (3.7× baseline) — buyer silent"
    BAD:  "Deal requires attention due to multiple signals"
    BAD:  "Issuer ROFR situation needs review"

  one_line_summary (≤160 chars): One sentence. Must contain: issuer name + at least ONE specific \
    number (days, date, ratio, percentage). Describes what is factually happening right now.
    GOOD: "Anthropic docs_pending 11 days (baseline 3d, ratio 3.7×); buyer has not returned docs after two requests."
    BAD:  "The deal has experienced a communication delay and may need analyst intervention."

  recommended_action (≤200 chars): Imperative sentence. Name the counterparty and a date/timeframe.
    GOOD: "Send ROFR waiver request to Stripe IR today; escalate to TS lead by EOD if no reply."
    BAD:  "Review the deal and consider reaching out to the relevant parties."

FORBIDDEN in any field:
  • "multiple signals", "various factors", "several concerns" (be specific)
  • "as soon as possible", "at your earliest convenience" (name a date)
  • "[Issuer]", "[DATE]" or any bracket placeholder
  • Passive voice in recommended_action

SPECIFICITY GATE: Before returning, confirm: (1) issuer name appears in headline AND summary, \
(2) at least one number appears in summary, (3) recommended_action names a counterparty and a date. \
If any fails, rewrite that field.

Reply only via the required tool.\
"""

_BRIEF_HUMAN = """\
Draft a brief entry for deal {deal_id} ({issuer_name}):

Stage: {stage}, severity: {severity}
Primary signals: {trigger_names}
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
    triggered = [s for s in signals if s.triggered]
    trigger_names = ", ".join(s.dimension.value for s in triggered) or "no major signals"
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "severity": severity.severity.value,
        "trigger_names": trigger_names,
        "severity_reasoning": severity.reasoning[:200],
    }
