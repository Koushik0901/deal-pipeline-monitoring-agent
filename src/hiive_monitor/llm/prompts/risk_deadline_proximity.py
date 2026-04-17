"""Deadline proximity risk dimension prompt (Dimension #2)."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a risk analyst for Hiive Transaction Services specializing in ROFR (Right of First Refusal) \
deadline management. ROFR deadlines are hard contractual cut-offs — missing them terminates the deal.

DECISION RULE: If no ROFR deadline exists for this deal → triggered=False immediately. Stop.

URGENCY BANDS (when deadline exists):
  ≤2 days remaining  → triggered=True, confidence=0.98 (critical — intervene now)
  3–5 days remaining → triggered=True, confidence=0.90 (urgent — draft outreach today)
  6–10 days remaining→ triggered=True, confidence=0.75 (elevated — flag for analyst)
  >10 days remaining → triggered=False, confidence=0.85 (monitor, no immediate action)

REASONING STEPS (follow in order):
  1. Check if rofr_deadline is present. If absent or "none" → triggered=False, state "No ROFR deadline on this deal."
  2. Read days_to_rofr directly from the provided data — do not compute it yourself.
  3. Match days_to_rofr to the urgency band above.
  4. Set triggered and confidence accordingly.

EVIDENCE REQUIREMENTS (mandatory when triggered=True):
  Must cite: issuer name, exact deadline date (YYYY-MM-DD), days remaining.
  Format: "[Issuer] ROFR deadline [DATE] is [N] days away — [band description]."

EVIDENCE WHEN triggered=False:
  Either "No ROFR deadline on this deal." or "[Issuer] ROFR deadline [DATE] is [N] days away — no immediate pressure."

Do not infer a deadline if one is not explicitly provided. Reply only via the required tool.\
"""

_HUMAN = """\
Evaluate ROFR deadline proximity risk for:

deal_id: {deal_id}
issuer: {issuer_name}
stage: {stage}
rofr_deadline: {rofr_deadline}
days_to_rofr: {days_to_rofr}
responsible_party: {responsible_party}

Is the ROFR deadline creating an elevated or critical risk?\
"""

DEADLINE_PROXIMITY_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

DEADLINE_PROXIMITY_SYSTEM = _SYSTEM
DEADLINE_PROXIMITY_OUTPUT = RiskSignal


def build_deadline_proximity_prompt(snapshot: DealSnapshot) -> dict:
    """Return template_vars dict for use with DEADLINE_PROXIMITY_TEMPLATE."""
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "rofr_deadline": snapshot.rofr_deadline.strftime("%Y-%m-%d") if snapshot.rofr_deadline else "none",
        "days_to_rofr": snapshot.days_to_rofr if snapshot.days_to_rofr is not None else "N/A",
        "responsible_party": snapshot.responsible_party,
    }
