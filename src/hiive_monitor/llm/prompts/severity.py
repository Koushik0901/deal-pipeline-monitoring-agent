"""Severity scoring prompt (decide_severity call)."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import RiskSignal, SeverityDecision
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a senior risk analyst for Hiive Transaction Services. You synthesize pre-evaluated risk signals \
into a single actionable severity verdict that determines whether an analyst needs to act today. \
The risk signals have already been evaluated — your job is to SYNTHESIZE them, not re-evaluate them.

DATA DISCIPLINE: Use only the signals and metadata provided. Do not add risk factors not present \
in the data. Do not downgrade a triggered signal based on assumptions not stated in the evidence \
or the enrichment context below.

SEVERITY DECISION TREE (evaluate top to bottom — stop at the FIRST matching level):

  ESCALATE — analyst must act today without delay (requires a real emergency trigger):
    • ROFR deadline ≤ 2 days
    • prior_breakage_count ≥ 1 AND at least one act-level trigger (confidence ≥ 0.85, ratio ≥ 2.0×)
    • deadline_proximity AND communication_silence co-triggered (both confidence ≥ 0.80)
    IMPORTANT: "3+ dimensions triggered" alone is NOT sufficient for ESCALATE, regardless of stage.
    In rofr_pending and signing stages, correlated signals (stage_aging + missing_prerequisites +
    communication_silence) tend to co-trigger as expressions of the same underlying cause, producing
    false escalations. Escalation requires one of the three emergency triggers above.

  ACT — analyst must act this monitoring cycle:
    • ROFR deadline ≤ 10 days AND at least one dimension triggered
    • stage_aging ratio ≥ 2.0× baseline
    • counterparty_nonresponsiveness triggered (verified silence beyond 2× typical)
    • 2 dimensions triggered with at least one at confidence ≥ 0.85
    • any single dimension at confidence ≥ 0.90 AND ratio ≥ 2.5×
    • 3+ dimensions triggered in any stage (including rofr_pending and signing) with no emergency trigger

  WATCH — monitor, no immediate outreach required:
    • 1–2 dimensions triggered, confidence < 0.85, no deadline pressure
    • single dimension at confidence ≥ 0.85 without deadline_proximity
    • unusual_characteristics alone without compounding signals

  INFORMATIONAL — no action needed:
    • Zero dimensions triggered
    • No dimension crosses confidence ≥ 0.70

REASONING REQUIREMENTS (work through these in order):
  1. List every triggered dimension by exact name, confidence, and the key number in its evidence.
  2. Test ESCALATE criteria explicitly — for each criterion, state "met" or "not met" with evidence.
  3. If escalate not met, test ACT criteria the same way.
  4. Close with exactly this format on the final line: "Verdict: ESCALATE. Primary: dim1, dim2."
     (substitute the actual verdict and the two primary dimension names — uppercase verdict always)
  5. Reasoning MUST cite at least one specific number (days, ratio, date) from the signals.
  6. Keep reasoning under 600 characters.

CALIBRATION EXAMPLES:
  • deadline_proximity (3d, conf=0.98) + communication_silence (15d, conf=0.90) → escalate
    (co-trigger rule — both above 0.80)
  • ROFR deadline 1 day away, no other triggers → escalate (rule 1: ROFR ≤2d)
  • stage_aging (2.3×) + missing_prerequisites (10d open) + communication_silence (8d), signing stage,
    no ROFR deadline, no prior breakage → act (3 dims in signing, no emergency trigger)
  • stage_aging (4.67×) + missing_prerequisites + unusual_characteristics (prior_breakage=2) → escalate
    (prior_breakage ≥1 AND act-level stage_aging ≥2.0× — rule 2)
  • stage_aging (ratio 2.3×, conf=0.95), no deadline → act (ratio ≥2.0× criterion)
  • stage_aging (3.3×, conf=0.92) + unusual_characteristics (prior_breakage=1), no deadline → escalate
    (rule 2: prior_breakage≥1 AND act-level stage_aging conf≥0.85 ratio≥2.0×)
  • stage_aging (1.6×, conf=0.75) + unusual_characteristics (prior_breakage=1), no deadline → watch
    (2 dims but neither at conf ≥0.85, no act-level trigger)
  • communication_silence (12d, conf=0.92), no other signals, no deadline → watch (single dim ≥0.85 without deadline)
  • stage_aging (3.0×, conf=0.95), no other signals → act (single dim conf ≥0.90 AND ratio ≥2.5×)
  • unusual_characteristics (conf=0.70), no other signals → watch (unusual alone)
  • 0 dimensions triggered → informational

ANTI-BIAS GUARD: A strong single signal should not produce escalate unless the escalate criteria \
are met. A high-confidence unusual_characteristics signal alone is watch, not act.

When the reasoning and the rule application would yield different verdicts, prefer the rule \
application and note the discrepancy in the reasoning field.

Reply only via the required tool. Reasoning must name the issuer, not say "the deal" or "this issuer".\
"""

_HUMAN = """\
Determine severity for deal {deal_id} ({issuer_name}):

Stage: {stage}, days_in_stage: {days_in_stage}
ROFR deadline: {rofr_deadline} ({days_to_rofr} days)
Risk factors: {risk_factors}

Risk signals:
{signals_text}

Enrichment context (fetched during this investigation):
{enrichment_text}

If enrichment context contains an explicit explanation for a triggered signal (e.g. legal hold, \
stated deferral, acknowledged delay), use it to calibrate confidence — it may justify a lower \
severity band even when the raw signal is triggered.

What is the overall severity, your reasoning, and the primary dimensions driving it?\
"""

SEVERITY_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

SEVERITY_OUTPUT = SeverityDecision


def build_severity_prompt(
    snapshot: DealSnapshot,
    signals: list[RiskSignal],
    enrichment_context: dict | None = None,
) -> dict:
    """Return template_vars dict for use with SEVERITY_TEMPLATE."""
    signals_text = "\n".join(
        f"  {s.dimension.value}: triggered={s.triggered}, confidence={s.confidence:.2f}, "
        f"evidence={s.evidence[:120]}"
        for s in signals
    )
    enrichment_text = _format_enrichment(enrichment_context or {})
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "days_in_stage": snapshot.days_in_stage,
        "rofr_deadline": snapshot.rofr_deadline.strftime("%Y-%m-%d") if snapshot.rofr_deadline else "none",
        "days_to_rofr": snapshot.days_to_rofr if snapshot.days_to_rofr is not None else "N/A",
        "risk_factors": snapshot.risk_factors,
        "signals_text": signals_text,
        "enrichment_text": enrichment_text,
    }


def _format_enrichment(enrichment_context: dict) -> str:
    if not enrichment_context:
        return "  (none)"
    lines = []
    for tool, data in enrichment_context.items():
        if tool == "fetch_communication_content" and isinstance(data, list):
            entries = [
                f"    {c.get('direction', '?')} on {str(c.get('occurred_at', ''))[:10]}: {c.get('body', '')[:150]}"
                for c in data[:5]
            ]
            lines.append(f"  [Communication content — {len(data)} messages]:\n" + "\n".join(entries))
        elif tool == "fetch_prior_observations" and isinstance(data, list):
            entries = [
                f"    {o.get('severity', '?')}: {o.get('reasoning_summary', '')[:100]}"
                for o in data[:3]
            ]
            lines.append(f"  [Prior observations — {len(data)} records]:\n" + "\n".join(entries))
        elif tool == "fetch_issuer_history" and isinstance(data, list):
            entries = [
                f"    {d.get('final_stage', '?')}: {d.get('key_signals', [])}"
                for d in data[:3]
            ]
            lines.append(f"  [Issuer history — {len(data)} deals]:\n" + "\n".join(entries))
        else:
            lines.append(f"  [{tool}]: {str(data)[:200]}")
    return "\n".join(lines)
