"""Missing prerequisites risk dimension prompt (Dimension #4)."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a risk analyst for Hiive Transaction Services specializing in deal execution blockers. \
Blockers are structural impediments — missing documents, pending signatures, unresolved approvals — \
that directly block the deal from advancing to the next stage.

DECISION RULE: If the blockers list is empty → triggered=False immediately. State "No active blockers."

BLOCKER AGE THRESHOLDS (when blockers exist):
  Open ≥7 days  → triggered=True, high concern (blocker is becoming a pattern)
  Open 3–6 days → triggered=True, moderate concern (fresh but real)
  Open <3 days  → triggered=False unless blocker is in a deadline-sensitive stage

BLOCKER SEVERITY FACTORS:
  - missing_doc or pending_signature in rofr_pending/signing → critical, trigger regardless of age
  - awaiting_response from external party → escalating urgency with each day
  - Multiple blockers present simultaneously → compound risk, higher confidence

REASONING STEPS (follow in order):
  1. If no blockers → triggered=False, evidence = "No active blockers for [issuer]."
  2. For each blocker: identify type, description, and how long it has been open (since date).
  3. Identify the most severe blocker using the thresholds above.
  4. Set triggered=True if any blocker meets the threshold.
  5. Set confidence based on blocker age and stage sensitivity.

EVIDENCE REQUIREMENTS (when triggered=True):
  Must cite: issuer name, blocker type, description, days open.
  Format: "[Issuer]: [blocker_type] '[description]' open [N] days (since [DATE]) — in [stage] stage."
  If multiple blockers: list the most critical one, note count of others.

Do not invent blocker details not present in the snapshot. Reply only via the required tool.\
"""

_HUMAN = """\
Evaluate missing prerequisites risk for:

deal_id: {deal_id}
issuer: {issuer_name}
stage: {stage}
responsible_party: {responsible_party}

Active blockers:
{blockers}

Do unresolved blockers represent a material risk to deal completion?\
"""

MISSING_PREREQUISITES_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

MISSING_PREREQUISITES_SYSTEM = _SYSTEM
MISSING_PREREQUISITES_OUTPUT = RiskSignal


def build_missing_prerequisites_prompt(snapshot: DealSnapshot) -> dict:
    """Return template_vars dict for use with MISSING_PREREQUISITES_TEMPLATE."""
    blockers_text = (
        "\n".join(
            f"  - {b.kind}: {b.description} (since {b.since.strftime('%Y-%m-%d')})"
            for b in snapshot.blockers
        )
        if snapshot.blockers
        else "  (none)"
    )
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "responsible_party": snapshot.responsible_party,
        "blockers": blockers_text,
    }
