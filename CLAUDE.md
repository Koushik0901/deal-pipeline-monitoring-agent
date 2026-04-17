# deal-pipeline-monitoring-agent Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-04-16

## Active Technologies

- Python 3.11+ (single language, single repo). + FastAPI, LangGraph (+ `langgraph-checkpoint-sqlite`), Anthropic Python SDK, Pydantic v2, APScheduler, Jinja2, HTMX (pinned), Alpine.js (pinned), Tailwind CSS (CLI build), structlog, PyYAML, pytest, uv (dep manager). (master)

## Project Structure

```text
backend/
frontend/
tests/
```

## Commands

cd src; pytest; ruff check .

## Code Style

Python 3.11+ (single language, single repo).: Follow standard conventions

## Recent Changes

- master: Added Python 3.11+ (single language, single repo). + FastAPI, LangGraph (+ `langgraph-checkpoint-sqlite`), Anthropic Python SDK, Pydantic v2, APScheduler, Jinja2, HTMX (pinned), Alpine.js (pinned), Tailwind CSS (CLI build), structlog, PyYAML, pytest, uv (dep manager).

<!-- MANUAL ADDITIONS START -->

## Design Context

### Users
Transaction Services analysts at Hiive. They are operationally focused, time-constrained, and spend their workday reading dense information: deal pipelines, risk signals, communication histories. They are not designers; they are expert operators who need speed and clarity.

### Brand Personality
**Three words:** Neutral, trustworthy, operational.

The interface should feel like a tool built by people who understand financial operations. No personality, no playfulness — just clarity and speed. The green brand color is a small mark of identity; it should not dominate.

### Aesthetic Direction
**Reference:** Linear.app — dense, minimal, professional.

**Theme:** Both light and dark mode (user toggleable)

**Color Palette:**
- **Brand primary:** #1B4332 (deep forest green) — active nav, primary buttons, informational severity, logo
- **Brand secondary:** #2D6A4F (lighter green) — accents, hover states
- **Semantic colors (override brand):** Escalate=red, Act=amber, Watch=blue, Informational=green
- **Neutrals:** Tinted toward brand hue (OKLCH chroma 0.005–0.01). No pure gray.

### Design Principles
1. **Information density over whitespace.** No excessive padding. Tables compact.
2. **Scannability first.** Severity, deal IDs, action items jump out. Hierarchy through typography weight/size.
3. **No decoration.** No side stripes, gradients, rounded-corner card stacking. Motion only for state changes.
4. **Trust through transparency.** Every risk flag carries visible reasoning. Agent is not a black box.
5. **Analyst control.** Agent suggests; analyst acts. No autonomous sends. Review before action.

<!-- MANUAL ADDITIONS END -->

