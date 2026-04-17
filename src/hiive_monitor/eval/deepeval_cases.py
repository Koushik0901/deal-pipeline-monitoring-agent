"""
deepeval LLMTestCase builder (TEV06).

Maps a YAML scenario dict + run_scenario() result dict to a deepeval
LLMTestCase suitable for all five metric evaluations.
"""

from __future__ import annotations

from deepeval.test_case import LLMTestCase, ToolCall


def _format_deal_snapshot(scenario: dict) -> str:
    """Render the deal setup as a structured text block for LLMTestCase.input."""
    setup = scenario.get("setup", {})
    deal = setup.get("deal", {})
    issuers = setup.get("issuers", [{}])
    parties = setup.get("parties", [])
    issuer = issuers[0] if issuers else {}

    buyer = next((p for p in parties if p.get("party_type") == "buyer"), {})
    seller = next((p for p in parties if p.get("party_type") == "seller"), {})

    events = deal.get("events", [])
    if events:
        recent = events[0]
        event_summary = (
            f"{len(events)} total events; most recent: "
            f"'{recent.get('event_type', '?')}' {recent.get('days_ago', '?')} days ago"
        )
    else:
        event_summary = "0 events (no communication or document history)"

    blockers = deal.get("blockers", [])
    blockers_text = (
        "; ".join(
            b.get("description", b.get("type", "unknown")) for b in blockers
        )
        if blockers
        else "none"
    )

    rofr_line = ""
    if deal.get("rofr_deadline_days_from_now") is not None:
        days = deal["rofr_deadline_days_from_now"]
        urgency = " [CRITICAL: expires today]" if days == 0 else (
            f" [urgent: {days} days]" if days <= 3 else ""
        )
        rofr_line = f"\nROFR deadline: {days} days from now{urgency}"

    multi_rofr = issuer.get("multi_layer_rofr", False)
    typical_days = issuer.get("typical_response_days")
    issuer_notes = []
    if multi_rofr:
        issuer_notes.append("multi-layer ROFR required")
    if typical_days:
        issuer_notes.append(f"typical response {typical_days} days")
    issuer_detail = f" ({'; '.join(issuer_notes)})" if issuer_notes else ""

    return (
        f"=== DEAL SNAPSHOT ===\n"
        f"Deal ID: {deal.get('deal_id', '?')}\n"
        f"Issuer: {issuer.get('name', issuer.get('issuer_id', '?'))}{issuer_detail}\n"
        f"Stage: {deal.get('stage', '?')} (entered {deal.get('stage_entered_days_ago', '?')} days ago)"
        f"{rofr_line}\n"
        f"Transaction: {deal.get('shares', '?')} shares @ ${deal.get('price_per_share', '?')}/share\n"
        f"Buyer: {buyer.get('display_name', '?')} "
        f"(first_time_buyer={buyer.get('is_first_time', False)}, "
        f"prior_breakage_count={buyer.get('prior_breakage_count', 0)})\n"
        f"Seller: {seller.get('display_name', '?')}\n"
        f"Responsible party: {deal.get('responsible_party', '?')}\n"
        f"Blockers: {blockers_text}\n"
        f"Event history: {event_summary}"
    )


def _format_actual_output(result: dict) -> str:
    """Render the agent's observation + intervention as the actual output."""
    severity = result.get("severity") or "informational"
    dims: list[str] = []
    interventions_count = result.get("interventions", 0)
    enrichment_rounds = 0

    for name, passed, detail in result.get("assertion_results", []):
        if name.startswith("dimension:") and passed:
            dims.append(name[len("dimension:"):])
        if name == "agent_triggers_enrichment" and "enrichment_rounds=" in (detail or ""):
            try:
                enrichment_rounds = int(detail.split("enrichment_rounds=")[1].split()[0])
            except (IndexError, ValueError):
                pass

    if interventions_count > 0:
        intervention_note = f"Intervention drafted: yes (count={interventions_count})"
    else:
        intervention_note = "Intervention drafted: no"

    return (
        f"=== AGENT OUTPUT ===\n"
        f"Severity assigned: {severity}\n"
        f"Risk dimensions triggered: {', '.join(dims) if dims else 'none'}\n"
        f"Enrichment tool calls made: {enrichment_rounds}\n"
        f"{intervention_note}"
    )


def _format_expected_output(scenario: dict) -> str:
    """Render the ground_truth block as the expected output."""
    gt = scenario.get("ground_truth", {})
    severity = gt.get("severity", "unknown")
    dims_triggered = gt.get("dimensions_triggered", [])
    dims_not_triggered = gt.get("dimensions_not_triggered", [])
    expected_tools = gt.get("expected_tools", [])

    lines = [
        "=== EXPECTED OUTPUT ===",
        f"Correct severity: {severity}",
        f"Dimensions that SHOULD be triggered: {', '.join(dims_triggered) if dims_triggered else 'none'}",
    ]
    if dims_not_triggered:
        lines.append(
            f"Dimensions that MUST NOT be triggered: {', '.join(dims_not_triggered)}"
        )
    if expected_tools:
        lines.append(f"Enrichment tools that should be called: {', '.join(expected_tools)}")
    elif gt.get("expected_tools") == []:
        lines.append("Enrichment tools that should be called: none (no enrichment expected)")
    return "\n".join(lines)


def _extract_context_facts(scenario: dict) -> list[str]:
    """
    Extract grounding facts for HallucinationMetric context.

    Each string is a verifiable fact from the fixture. The HallucinationMetric
    checks that the agent's intervention body does not assert anything that
    contradicts or is absent from this list.
    """
    setup = scenario.get("setup", {})
    deal = setup.get("deal", {})
    issuers = setup.get("issuers", [{}])
    issuer = issuers[0] if issuers else {}
    now = setup.get("now", "")

    issuer_name = issuer.get("name", issuer.get("issuer_id", ""))
    facts = [
        f"The issuer company name is: {issuer_name}",
        f"The deal ID is: {deal.get('deal_id', '')}",
        f"The current pipeline stage is: {deal.get('stage', '')}",
        f"The deal has been in this stage for {deal.get('stage_entered_days_ago', 0)} days",
        f"Share count in this transaction: {deal.get('shares', '')} shares",
        f"Price per share: ${deal.get('price_per_share', '')}",
        f"Today's date (evaluation date): {now[:10] if now else ''}",
        f"Responsible party for next action: {deal.get('responsible_party', '')}",
    ]

    if deal.get("rofr_deadline_days_from_now") is not None:
        from datetime import datetime, timedelta
        try:
            now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
            deadline = now_dt + timedelta(days=deal["rofr_deadline_days_from_now"])
            facts.append(f"ROFR (Right of First Refusal) deadline date: {deadline.strftime('%Y-%m-%d')}")
            facts.append(f"Days remaining until ROFR deadline: {deal['rofr_deadline_days_from_now']}")
        except (ValueError, KeyError):
            pass

    parties = setup.get("parties", [])
    for p in parties:
        ptype = p.get("party_type", "party").title()
        name = p.get("display_name", "")
        if name:
            facts.append(f"{ptype} display name: {name}")
        if p.get("is_first_time"):
            facts.append(f"The {ptype.lower()} is a first-time participant on Hiive")
        if p.get("prior_breakage_count", 0) > 0:
            facts.append(
                f"The {ptype.lower()} has {p['prior_breakage_count']} prior deal breakage(s) on record"
            )

    blockers = deal.get("blockers", [])
    for b in blockers:
        desc = b.get("description", b.get("type", ""))
        if desc:
            facts.append(f"Active blocker: {desc}")

    for ev in deal.get("events", []):
        body = ev.get("payload", {}).get("body", "")
        etype = ev.get("event_type", "")
        days_ago = ev.get("days_ago", "?")
        if body:
            facts.append(
                f"Communication ({etype}, {days_ago} days ago): \"{body[:300]}\""
            )
        elif etype:
            facts.append(f"Event on record: {etype}, occurred {days_ago} days ago")

    if issuer.get("multi_layer_rofr"):
        facts.append("This issuer requires multi-layer ROFR approval (multiple internal sign-offs)")
    if issuer.get("typical_response_days"):
        facts.append(f"Issuer's typical response time: {issuer['typical_response_days']} days")

    return [f for f in facts if f.split(": ", 1)[-1].strip()]


def _extract_tools_called(result: dict) -> list[ToolCall]:
    """Reconstruct ToolCall list from enrichment_chain stored in observation reasoning."""
    # enrichment_chain is stored in the run result if we parse it from assertion_results
    # We look for it via the agent_triggers_enrichment assertion context
    tools: list[ToolCall] = []
    for name, _passed, detail in result.get("assertion_results", []):
        if name == "enrichment_tool_called":
            # detail format: "called=[tool1, tool2]"
            # Extract from assertion result detail
            pass
    # Fall back: check enrichment_chain from assertion detail
    for name, passed, detail in result.get("assertion_results", []):
        if name == "agent_triggers_enrichment" and passed and "enrichment_rounds=" in detail:
            pass
    # Note: full enrichment_chain parsing requires passing raw observations;
    # the ToolCall list here is derived from what was asserted in the fixture.
    return tools


def build_test_case(scenario: dict, result: dict) -> LLMTestCase:
    """Build a deepeval LLMTestCase from a scenario dict + run_scenario() result."""
    # Reconstruct tools_called from enrichment assertions
    tools_called: list[ToolCall] = []
    deal_id = scenario.get("setup", {}).get("deal", {}).get("deal_id", "")
    issuer_id = scenario.get("setup", {}).get("deal", {}).get("issuer_id", "")

    for name, passed, _detail in result.get("assertion_results", []):
        if name == "enrichment_tool_called" and passed:
            # tool name is stored in assertions block
            tool_name = scenario.get("assertions", {}).get("enrichment_tool_called", "")
            if tool_name:
                arg = "issuer_id" if tool_name == "fetch_issuer_history" else "deal_id"
                arg_val = issuer_id if tool_name == "fetch_issuer_history" else deal_id
                tools_called.append(ToolCall(name=tool_name, input_parameters={arg: arg_val}))

    return LLMTestCase(
        input=_format_deal_snapshot(scenario),
        actual_output=_format_actual_output(result),
        expected_output=_format_expected_output(scenario),
        context=_extract_context_facts(scenario),
        tools_called=tools_called if tools_called else None,
    )


def build_expected_tools(scenario: dict) -> list[ToolCall] | None:
    """Build expected ToolCall list from ground_truth.expected_tools."""
    gt = scenario.get("ground_truth", {})
    expected = gt.get("expected_tools", [])
    if not expected:
        return None
    deal_id = scenario.get("setup", {}).get("deal", {}).get("deal_id", "")
    issuer_id = scenario.get("setup", {}).get("deal", {}).get("issuer_id", "")
    calls = []
    for tool in expected:
        arg = "issuer_id" if tool == "fetch_issuer_history" else "deal_id"
        arg_val = issuer_id if tool == "fetch_issuer_history" else deal_id
        calls.append(ToolCall(name=tool, input_parameters={arg: arg_val}))
    return calls or None
