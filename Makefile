PORT ?= 8000
.PHONY: setup seed run eval demo clean test lint

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
	uv run uvicorn hiive_monitor.app:app --host 0.0.0.0 --port $(PORT) --reload

# ── Eval ─────────────────────────────────────────────────────────────────────
eval:
	uv run python -m hiive_monitor.eval.runner

# ── Demo ─────────────────────────────────────────────────────────────────────
# Seeds DB, runs 3 simulated ticks (fast-forwarding clock), then starts the app.
demo: seed
	@echo "Seeded 30 deals. Running 3 simulated ticks to populate the brief..."
	CLOCK_MODE=simulated uv run python -m hiive_monitor.seed.demo_ticks
	@echo "Starting app → http://localhost:8000/brief"
	CLOCK_MODE=simulated uv run uvicorn hiive_monitor.app:app --host 0.0.0.0 --port $(PORT)

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
