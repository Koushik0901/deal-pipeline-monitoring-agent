# HTTP Route Contracts

FastAPI + Jinja2 + HTMX. All routes are single-user, localhost, no auth. HTML routes return full pages on direct GET; HTMX-triggered requests (`HX-Request` header present) return fragments. JSON routes exist only for the eval harness and simulation controls.

## HTML routes (analyst UI)

| Method | Path | Returns | Purpose |
|---|---|---|---|
| GET | `/` | redirect → `/brief` | Landing. |
| GET | `/brief` | full page or `_brief_list.html` fragment | Daily Brief — top 5–7 ranked items with drafted interventions. |
| GET | `/brief/all-open` | full page or `_all_open_list.html` fragment | "All Open Items" tab — every act/escalate across deals, filterable by severity/stage/issuer. |
| GET | `/pipeline` | full page | Book-of-deals view — every live deal, one row, deterministic health tier. Filters (tier/stage/issuer/responsible/text) and sort are **client-side only**; server always renders all rows. |
| GET | `/queue` | 307 → `/pipeline` | Back-compat redirect; preserves query string. |
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

**Pipeline filter semantics** (`/pipeline`):
- Query params (`tier`, `stage`, `issuer`, `responsible`, `sort`) are accepted for deep-linking and no-JS fallback only. They set the initial `hidden` class on non-matching rows via Jinja2; they do **not** filter the server-side row set.
- Once JS is loaded, the `window.PF` controller reads the same URL params, populates the control widgets, and applies instant visibility toggles via `data-*` attributes. No server round-trip on filter/sort changes.
- `counts_by_tier` in the header reflects the whole pipeline, not the filtered slice — book-wide triage context is preserved regardless of the active filter.

**View Transitions** (same-document and cross-document):
- Severity badge: `view-transition-name: sev-{deal_id}` on Brief rows (`_macros.html`), Pipeline rows (`pipeline.html`), and the deal-detail header (`deal_detail.html`).
- Deal ID text: `view-transition-name: dealid-{deal_id}` in the same three places.
- Matching names cause the clicked row's badge + ID to morph into the detail page during navigation. Non-matching names crossfade with the root. Firefox has no cross-document VT support → navigation works, just without the morph.

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
