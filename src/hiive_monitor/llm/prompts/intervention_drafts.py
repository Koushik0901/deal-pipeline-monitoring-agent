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
This email will be reviewed and sent as-is (or with minor edits). Every word must be specific, \
credible, and actionable. Vague emails erode analyst trust and delay deals.

MANDATORY CONTENT — all four must appear in the body:
  1. The issuer's name (use the exact display name provided — never "[Issuer]" or a placeholder)
  2. The current deal stage and how long it has been there
  3. If a deadline exists: the exact calendar date in YYYY-MM-DD format
  4. A single, specific request with a concrete deadline or timeframe for response

VOICE AND STYLE:
  • Transaction Services analyst tone: warm, direct, professional
  • No hedging phrases ("I just wanted to", "I was hoping to", "if you get a chance")
  • No filler openings ("I hope this finds you well")
  • No signature block — analyst will add their own
  • No em-dashes (—) or overly formal language
  • Active voice only

STRUCTURE: Subject line + email body. Body: 3–5 sentences max. Under 1200 characters total.

QUALITY CHECK before outputting: Does the body contain a specific date, the issuer name, \
and exactly one clear action item? If not, rewrite until it does.

NEVER output: placeholder text in brackets, "TODO", generic language not specific to this deal. \
Reply only via the required tool.\
"""

_OUTBOUND_HUMAN = """\
Draft an outbound nudge for deal {deal_id}:

Issuer: {issuer_name}
Stage: {stage} ({days_in_stage} days)
{deadline_text}
Responsible party: {responsible_party}
Severity: {severity}
Key risk signals: {trigger_text}{missing_docs_instruction}{deadline_instruction}

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
    trigger_text = "; ".join(f"{s.dimension.value}: {s.evidence[:100]}" for s in triggered)
    if snapshot.rofr_deadline and snapshot.days_to_rofr is not None:
        deadline_date = snapshot.rofr_deadline.strftime("%Y-%m-%d")
        deadline_text = f"ROFR deadline: {deadline_date} ({snapshot.days_to_rofr} days remaining)"
        deadline_instruction = (
            f"\nREQUIRED: Your email body MUST include the exact deadline date in ISO format: "
            f"{deadline_date} — use this exact format, do not write it as a month name."
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

    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "days_in_stage": snapshot.days_in_stage,
        "deadline_text": deadline_text,
        "responsible_party": snapshot.responsible_party,
        "severity": severity.severity.value,
        "trigger_text": trigger_text or "none",
        "missing_docs_instruction": missing_docs_instruction,
        "deadline_instruction": deadline_instruction,
    }


# ── Internal escalation ───────────────────────────────────────────────────────

_ESCALATION_SYSTEM = """\
You are drafting an internal escalation note for a Hiive Transaction Services analyst to route \
to the appropriate internal team. This note will be read by a TS lead, legal counsel, or ops \
manager who does not have the deal context — be complete and specific.

ROUTING GUIDANCE (choose based on the risk signals):
  • TS lead: stage stalls, counterparty unresponsiveness, general deal health concerns
  • Legal: ROFR legal complexity, multi-layer ROFR disputes, regulatory concerns
  • Ops: documentation blockers, signature collection delays, process failures

MANDATORY CONTENT:
  1. Deal ID and issuer name in the first sentence
  2. The specific problem: what is blocked, for how long
  3. What has already been attempted (if evident from signals)
  4. A single concrete suggested next step (who does what by when)

VOICE: Factual, urgent, internal. No politeness preamble. Lead with the problem.

STRUCTURE:
  body: 3–5 sentences stating the situation and the ask. Under 800 characters.
  suggested_next_step: One sentence, imperative mood, specific owner and action. Under 200 characters.
    Good: "TS lead to call Stripe IR contact by [DATE] and confirm ROFR waiver status."
    Bad: "Follow up with the issuer as soon as possible."

NEVER output: vague next steps, placeholder text, hedged language. \
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
    trigger_text = "; ".join(f"{s.dimension.value}: {s.evidence[:120]}" for s in triggered)
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
The analyst will read 5–7 of these in 2 minutes. Every entry must be scan-readable and specific — \
no entry should look like it could belong to a different deal.

THREE FIELDS TO FILL:

  headline (≤100 chars): "[Issuer] — [the core risk in 5–8 words]"
    Good: "Stripe ROFR deadline in 3 days — outreach needed"
    Bad: "Deal requires attention due to multiple signals"

  one_line_summary (≤160 chars): One sentence with at least one specific number (days, date, %) \
    and the issuer name. Describes WHAT is happening.
    Good: "Anthropic docs_pending stage 11 days (baseline 3d); buyer has not responded to two requests."
    Bad: "The deal has experienced a communication delay and may need intervention."

  recommended_action (≤200 chars): Imperative sentence. WHAT should the analyst do, and by WHEN.
    Good: "Send ROFR waiver request to Stripe IR today; escalate to TS lead if no response by EOD."
    Bad: "Review the deal and consider reaching out to the appropriate parties."

SPECIFICITY RULE: If your output could apply to a different deal by swapping the name, rewrite it. \
Every entry must contain the issuer name, a specific number, and a specific action.

NEVER output: generic statements, vague actions, placeholder brackets, hedged language. \
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
