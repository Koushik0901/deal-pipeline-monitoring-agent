PORT ?= 8000
.PHONY: setup seed run eval eval-deep langfuse-up langfuse-down demo clean test lint

# ── Setup ────────────────────────────────────────────────────────────────────
setup:
	uv sync --extra dev
	uv run python -m hiive_monitor.db.init
	npm install tailwindcss@3 --save-dev --silent
	npx tailwindcss -i ./src/hiive_monitor/web/static/input.css -o ./src/hiive_monitor/web/static/output.css --minify
	@echo ""
	@echo "✓ Setup complete."
	@echo "  Next: copy .env.example → .env and set OPENROUTER_API_KEY."
	@echo "  Then: make demo"

# ── Seed ─────────────────────────────────────────────────────────────────────
seed:
	uv run python -m hiive_monitor.seed.seed_data --reset

# ── Run ──────────────────────────────────────────────────────────────────────
run:
	uv run uvicorn hiive_monitor.app:app --host 127.0.0.1 --port $(PORT)

# ── Eval (Tier 1: run agents on fixture golden set) ──────────────────────────
eval:
	uv run python -m hiive_monitor.eval.runner

# ── Deep Eval (Tier 2: LLM-as-judge on saved Tier 1 results) ─────────────────
# Run 'make eval' first to generate eval_results/results_latest.json
eval-deep: export DEEPEVAL_TELEMETRY_OPT_OUT := YES
eval-deep:
	uv run python -m hiive_monitor.eval.deepeval_runner

# ── Langfuse dashboard (open-source, self-hosted) ────────────────────────────
# Requires Docker. First run: open http://localhost:3000 and create an account,
# then copy keys into .env as LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.
langfuse-up:
	docker compose -f docker-compose.langfuse.yml up -d

langfuse-down:
	docker compose -f docker-compose.langfuse.yml down

# ── Demo ─────────────────────────────────────────────────────────────────────
# Seeds DB, runs 3 simulated ticks (fast-forwarding clock), then starts the app.
demo: export CLOCK_MODE := simulated
demo: seed
	@echo "Seeded 44 deals. Running 3 simulated ticks to populate the brief..."
	uv run python -m hiive_monitor.seed.demo_ticks
	@echo "Starting app → http://localhost:8000/brief"
	uv run uvicorn hiive_monitor.app:app --host 127.0.0.1 --port $(PORT)

# ── Test ─────────────────────────────────────────────────────────────────────
test:
	uv run python -m pytest tests/ -v

lint:
	uv run ruff check src/ tests/

# ── Clean ────────────────────────────────────────────────────────────────────
clean:
	rm -f domain.db agent_checkpoints.db
	rm -f src/hiive_monitor/web/static/output.css
	rm -rf eval_results/
	rm -rf __pycache__ src/**/__pycache__ tests/**/__pycache__ .pytest_cache
	find . -name "*.pyc" -delete
	@echo "✓ Clean."
