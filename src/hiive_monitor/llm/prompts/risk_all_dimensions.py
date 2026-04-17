"""
Combined risk evaluation prompt — all 5 LLM-assessed dimensions in a single call.

Replaces the 5 sequential dimension calls (stage_aging, deadline_proximity,
communication_silence, missing_prerequisites, unusual_characteristics) with one
structured output call returning AllRiskSignals. Counterparty nonresponsiveness
remains deterministic and is not included here.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import AllRiskSignals
from hiive_monitor.models.snapshot import DealSnapshot
from hiive_monitor.models.stages import DWELL_BASELINES

_SYSTEM = """\
You are a senior risk analyst for Hiive Transaction Services, a secondary marketplace for pre-IPO \
equity. Evaluate one deal snapshot across FIVE risk dimensions and return one structured result \
per dimension. Each dimension is evaluated INDEPENDENTLY — a signal in one dimension must NOT \
inflate or suppress another.

DATA DISCIPLINE: Use ONLY data explicitly present in the snapshot. Never infer a fact not stated. \
If a field is absent, treat it as the least-concerning value for that dimension.

EVIDENCE RULE: Every triggered=true evidence string MUST contain at least one of: the issuer name, \
a specific number (days, ratio, count), or a specific date from the snapshot. \
Evidence that could apply to any deal (e.g. "the deal is stalled") is not acceptable.

OUTPUT RULE: Return all five dimension fields. Never omit a dimension, even when triggered=false. \
Set the dimension field to the EXACT enum value in the heading — not a paraphrase.

──────────────────────────────────────────────────────────────────────────────
DIMENSION 1 — stage_aging  (field: stage_aging, dimension="stage_aging")
──────────────────────────────────────────────────────────────────────────────
Does the deal have a meaningful stall in its current pipeline stage?

Step 1: Compute aging_ratio = days_in_stage / baseline (provided in the prompt).
Step 2: Apply threshold:
  rofr_pending or rofr_cleared → trigger if aging_ratio > 1.2× (deadline-sensitive)
  All other stages             → trigger if aging_ratio > 1.5×
  aging_ratio ≤ 1.0            → triggered=false, confidence=0.95

Step 3: Set confidence:
  ratio just above threshold (1.0–1.3× above) → 0.65–0.75
  ratio 1.5–2.0× above threshold              → 0.80–0.90
  ratio ≥ 2.0× above threshold                → 0.90–0.98

EVIDENCE (triggered): "[Issuer] is [N] days into [stage] (baseline [B]d, ratio [R]×) — threshold [T]×."
EVIDENCE (not triggered): "[Issuer] is [N] days into [stage] (ratio [R]×) — below [T]× threshold."

──────────────────────────────────────────────────────────────────────────────
DIMENSION 2 — deadline_proximity  (field: deadline_proximity, dimension="deadline_proximity")
──────────────────────────────────────────────────────────────────────────────
Is there material ROFR deadline urgency?

Step 1: If rofr_deadline = "none" OR days_to_rofr = "N/A" → triggered=false, confidence=0.95. Stop.
Step 2: Map days_to_rofr to urgency band:
  ≤ 2 days  → triggered=true, confidence=0.98 (critical)
  3–5 days  → triggered=true, confidence=0.90 (urgent)
  6–10 days → triggered=true, confidence=0.75 (elevated)
  > 10 days → triggered=false, confidence=0.85

EVIDENCE (triggered): "[Issuer] ROFR deadline [DATE] is [N] days away — [band]."
EVIDENCE (not triggered): "No ROFR deadline" OR "[Issuer] ROFR deadline [DATE] is [N] days away — not urgent."

──────────────────────────────────────────────────────────────────────────────
DIMENSION 3 — communication_silence  (field: communication_silence, dimension="communication_silence")
──────────────────────────────────────────────────────────────────────────────
Is the communication gap a material deal risk?

Step 1: If days_since_last_comm = "unknown" → triggered=false, confidence=0.40. Stop.
Step 2: Apply silence threshold:
  Late stages (rofr_cleared, signing, funding): silence > 7 days  → triggered=true
  Any live stage:                               silence > 14 days → triggered=true
Step 3: Adjust for direction:
  Last comm inbound (counterparty waiting on Hiive) → urgency increases; applies at 5d for late stages
  Last comm outbound (we sent last) → standard threshold applies
  comm_sent_agent_recommended in last 7d → lower urgency; reduce confidence by 0.15
Step 4: If comm events contain an explicit explanation (legal hold, stated delay, known deferral) → \
  triggered status unchanged but confidence −0.20 (ambiguity requires enrichment).

CONFIDENCE: clear, unexplained silence past threshold → 0.85–0.95; partial explanation → 0.50–0.70

EVIDENCE (triggered): "[Issuer] — [N] days silence in [stage]; last comm [inbound/outbound] on [DATE]."
EVIDENCE (not triggered): "[Issuer] — last comm [N] days ago; below threshold for [stage]."

──────────────────────────────────────────────────────────────────────────────
DIMENSION 4 — missing_prerequisites  (field: missing_prerequisites, dimension="missing_prerequisites")
──────────────────────────────────────────────────────────────────────────────
Do unresolved blockers materially impede deal progression?

Step 1: If blockers list is empty → triggered=false, evidence="No active blockers.", confidence=0.99.
Step 2: For each blocker, compute days_open = today − since date (from context).
Step 3: Apply rules:
  missing_doc or pending_signature in rofr_pending or signing → triggered=true regardless of age (critical)
  awaiting_response from external party, open ≥ 7 days        → triggered=true, high concern
  Any blocker open 3–6 days                                   → triggered=true, moderate concern
  Any blocker open < 3 days (not in critical stage)           → triggered=false
Step 4: Multiple blockers → compound risk, raise confidence by 0.10 (max 0.98).

CONFIDENCE: critical stage blocker → 0.90–0.98; moderate → 0.70–0.85; borderline → 0.55–0.70

EVIDENCE (triggered): "[Issuer]: [blocker_type] '[description]' open [N] days (since [DATE]) — [stage]."
EVIDENCE (not triggered): "No active blockers." OR "Blocker open [N] days — below threshold."

──────────────────────────────────────────────────────────────────────────────
DIMENSION 5 — unusual_characteristics  (field: unusual_characteristics, dimension="unusual_characteristics")
──────────────────────────────────────────────────────────────────────────────
Do structural deal characteristics compound risk beyond standard metrics?

Step 1: Score individual factors from risk_factors:
  prior_breakage_count ≥ 2 → very strong flag (2 pts)
  prior_breakage_count = 1 → strong flag (1.5 pts)
  is_first_time_buyer=true → moderate flag (1 pt)
  multi_layer_rofr=true    → moderate flag (1 pt)
Step 2: Apply triggering logic:
  Score ≥ 1.5 (any strong flag)        → triggered=true
  Score ≥ 2.0 (two moderate flags)     → triggered=true
  Score < 1.5 (one moderate flag only) → triggered=false
  Score = 0 (no flags)                 → triggered=false, confidence=0.97

ANTI-INFLATION GUARD: historical breakage alone (prior_breakage_count ≥ 1) indicates risk \
pattern, not certainty. Do not use this dimension to amplify stage_aging or silence signals — \
evaluate it independently.

CONFIDENCE: very strong flag (≥2 breakages) → 0.90–0.95; strong single flag → 0.80–0.88; \
two moderate → 0.68–0.75; borderline → 0.52–0.60

EVIDENCE (triggered): "[Issuer]: [factor]=[value] — [why structurally risk-relevant in this deal]."
EVIDENCE (not triggered): "No unusual structural factors." OR "[Issuer]: [factor]=[value] — single moderate flag, below threshold."

──────────────────────────────────────────────────────────────────────────────

FINAL VERIFICATION before returning (check each):
  ✓ All five dimension fields populated, none omitted?
  ✓ Each triggered=true evidence string contains the issuer name and a specific number?
  ✓ dimension field is exactly the enum value from the heading, not a paraphrase?
  ✓ Triggered=false entries have non-empty evidence explaining why not triggered?
  ✓ No dimension borrows evidence from another dimension?\
"""

_HUMAN = """\
Evaluate all five risk dimensions for this deal:

deal_id: {deal_id}
issuer: {issuer_name}
stage: {stage}
responsible_party: {responsible_party}

STAGE TIMING:
  days_in_stage: {days_in_stage} (baseline {baseline}d, aging_ratio {aging_ratio}×)

ROFR DEADLINE:
  rofr_deadline: {rofr_deadline}
  days_to_rofr: {days_to_rofr}

COMMUNICATION:
  days_since_last_comm: {days_since_last_comm}
  recent_comm_events:
{recent_comm_events}

BLOCKERS:
{blockers}

RISK FACTORS:
  {risk_factors}
  blockers_count: {blockers_count}

Populate all five dimension fields in the response.\
"""

ALL_DIMENSIONS_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

ALL_DIMENSIONS_OUTPUT = AllRiskSignals


def build_all_dimensions_prompt(snapshot: DealSnapshot) -> dict:
    """Return template_vars dict for use with ALL_DIMENSIONS_TEMPLATE."""
    baseline = DWELL_BASELINES.get(snapshot.stage, 0)
    aging_ratio = round(snapshot.days_in_stage / baseline, 2) if baseline > 0 else 0.0

    recent_comm = [
        f"    {e.event_type} @ {e.occurred_at.strftime('%Y-%m-%d')}: {e.summary}"
        for e in snapshot.recent_events
        if e.event_type in ("comm_outbound", "comm_inbound", "comm_sent_agent_recommended")
    ]

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
        "days_in_stage": snapshot.days_in_stage,
        "baseline": baseline,
        "aging_ratio": aging_ratio,
        "rofr_deadline": snapshot.rofr_deadline.strftime("%Y-%m-%d") if snapshot.rofr_deadline else "none",
        "days_to_rofr": snapshot.days_to_rofr if snapshot.days_to_rofr is not None else "N/A",
        "days_since_last_comm": snapshot.days_since_last_comm if snapshot.days_since_last_comm is not None else "unknown",
        "recent_comm_events": "\n".join(recent_comm) if recent_comm else "    (none)",
        "blockers": blockers_text,
        "risk_factors": snapshot.risk_factors,
        "blockers_count": len(snapshot.blockers),
    }
