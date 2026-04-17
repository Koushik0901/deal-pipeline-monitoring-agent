"""Missing prerequisites risk dimension prompt (Dimension #4)."""

from __future__ import annotations

from hiive_monitor.models.risk import RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a risk analyst for Hiive Transaction Services. Evaluate whether a deal has \
unresolved blockers that materially impede progress.

Trigger the signal if there are active blockers (missing docs, pending signatures, \
awaiting responses) that have been open long enough to create stall risk. Evidence MUST \
cite the issuer name, the specific blocker type and description, and how long it has been open. \
Reply only via the required tool."""


def build_missing_prerequisites_prompt(snapshot: DealSnapshot) -> str:
    blockers_text = (
        "\n".join(
            f"  - {b.kind}: {b.description} (since {b.since.strftime('%Y-%m-%d')})"
            for b in snapshot.blockers
        )
        if snapshot.blockers
        else "  (none)"
    )
    return f"""\
Evaluate missing prerequisites risk for:

deal_id: {snapshot.deal_id}
issuer: {snapshot.issuer_name}
stage: {snapshot.stage.value}
responsible_party: {snapshot.responsible_party}

Active blockers:
{blockers_text}

Do unresolved blockers represent a material risk to deal completion?"""


MISSING_PREREQUISITES_SYSTEM = _SYSTEM
MISSING_PREREQUISITES_OUTPUT = RiskSignal
