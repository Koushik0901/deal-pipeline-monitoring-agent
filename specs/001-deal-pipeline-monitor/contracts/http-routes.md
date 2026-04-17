# HTTP Route Contracts

FastAPI + Jinja2 + HTMX. All routes are single-user, localhost, no auth. HTML routes return full pages on direct GET; HTMX-triggered requests (`HX-Request` header present) return fragments. JSON routes exist only for the eval harness and simulation controls.

## HTML routes (analyst UI)

| Method | Path | Returns | Purpose |
|---|---|---|---|
| GET | `/` | redirect → `/brief` | Landing. |
| GET | `/brief` | full page or `_brief_list.html` fragment | Daily Brief — top 5–7 ranked items with drafted interventions. |
| GET | `/brief/all-open` | full page or `_all_open_list.html` fragment | "All Open Items" tab — every act/escalate across deals, filterable by severity/stage/issuer. |
| GET | `/deals/{deal_id}` | full page or `_deal_detail.html` fragment | Per-deal drill-down: facts header, event timeline, agent observation history, intervention history. |
| POST | `/interventions/{intervention_id}/approve` | `_brief_item.html` (OOB status bar) | Marks intervention `approved`, returns swapped list-item with "handled" badge. |
| POST | `/interventions/{intervention_id}/edit` | `_brief_item.html` | Form body: `body`, `subject?`, `recipient_name?`. Persists edit, marks `edited`. |
| POST | `/interventions/{intervention_id}/dismiss` | `_brief_item.html` | Marks intervention `dismissed`. |
| POST | `/sim/advance` | `_brief_list.html` fragment | Form body: `days: int`. Advances SimulatedClock by N days, fires N sequential ticks, returns refreshed brief. 400 if `CLOCK_MODE != simulated`. |
| GET | `/debug/tick/{tick_id}` | full page | Raw structured-log view for a tick. Gated on `?debug=1` query param presence on any page. |
| GET | `/debug/deal/{deal_id}` | full page | Raw structured-log view for a deal across ticks. |

**Fragment discipline**:
- Partial templates are prefixed `_` (e.g., `_brief_item.html`).
- `hx-target` + `hx-swap="outerHTML"` on list items for in-place replacement.
- `hx-swap-oob="true"` used on status-bar count so a single action response updates two regions.
- Real-time mode: brief container has `hx-trigger="every 30s"` polling `/brief` (`HX-Request` makes the server return the fragment).
- Simulated mode: no polling; `/sim/advance` POST is the only refresh trigger.

**Redirect/push-url**:
- `hx-push-url="true"` on deal drill-down links so the back button works.

**CSRF**: not required (single-user localhost prototype). Documented as assumption.

## JSON routes (eval harness only)

| Method | Path | Body | Response | Purpose |
|---|---|---|---|---|
| POST | `/api/eval/reset` | `{}` | `{ok: true}` | Truncates `domain.db` except schema. |
| POST | `/api/eval/setup` | scenario `setup:` block (YAML-derived JSON) | `{ok: true}` | Applies scenario fixtures, sets SimulatedClock. |
| POST | `/api/eval/tick` | `{}` | `{tick_id, deals_screened, deals_investigated, observations: [...], interventions: [...]}` | Runs exactly one Monitor tick, returns results. |
| GET | `/api/eval/observations?tick_id=...&deal_id=...` | — | `[AgentObservation]` | Fetches observations matching filter. |
| GET | `/api/eval/brief?tick_id=...` | — | `DailyBrief` | Fetches composed brief for a tick. |

JSON payloads use the Pydantic contract models from [data-model.md](../data-model.md) verbatim.

## Error responses

- Validation errors (Pydantic): HTTP 422 with standard FastAPI body for JSON routes; HTMX toast fragment for HTML routes.
- Missing entity: HTTP 404.
- Advance in real-time mode: HTTP 400 `{"detail":"sim.advance unavailable in real_time mode"}`.
- LLM parse failure after corrective reprompt: persisted as an error observation (severity `informational`, reasoning prefixed `[llm-error]`), UI renders an "observation unavailable" row. Route does not 500.

## Invariants

- **No route mutates deal stage.** Stage transitions happen only via simulated events / the seed script / future real integrations. Analyst actions affect only `interventions` rows.
- **No route sends anything externally.** The intervention module has no send method; approving an intervention merely copies the body to the clipboard (Alpine.js) and marks the row approved.
- **Idempotency at the write layer, not the route.** Double-POSTing an approve action is safe because the DB row transitions are status-only.
- **Approve and edit-confirm emit a loop-closure event atomically.** Both `POST /interventions/{id}/approve` and `POST /interventions/{id}/confirm-edit` MUST emit a `comm_sent_agent_recommended` event in the same database transaction as the intervention status update. This is not optional — a partial write (status updated but event not emitted, or vice versa) breaks the communication loop closure logic in the Pipeline Monitor (FR-LOOP-01/02/03) and will cause re-flagging of already-actioned deals on the next tick. Both handlers call `dao.approve_intervention_with_event(intervention_id, simulated_timestamp)` which wraps both writes atomically.
