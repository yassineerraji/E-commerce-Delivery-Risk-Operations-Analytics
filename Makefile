# Public commands. MODE selects the database and artifact folder (real by default; test modes are
# reserved for the automated tests).
# Expensive or destructive steps are explicit; nothing downloads data implicitly.

MODE ?= real
UV ?= uv
RUN := $(UV) run delivery-risk
COMPOSE := docker compose --env-file .env

.DEFAULT_GOAL := help
.PHONY: help setup db-up db-down db-reset ingest transform quality features train evaluate \
        benchmark dashboard dashboard-docker test test-fast lint format report final-report reproduce screenshots

help: ## list the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-18s %s\n", $$1, $$2}'

setup: ## install locked dependencies; create a local .env with random passwords if missing
	$(UV) sync --frozen
	$(RUN) setup-env

db-up: ## start PostgreSQL 17 (published on 127.0.0.1 only), wait for health, create roles/databases/schemas
	$(COMPOSE) up -d --wait db
	$(RUN) db-init

db-down: ## stop the database container (the data volume is kept)
	$(COMPOSE) stop db

db-reset: ## DESTRUCTIVE: drop and recreate the project schemas of MODE only (needs CONFIRM=yes)
	@test "$(CONFIRM)" = "yes" || (echo "Refusing. Run: make db-reset MODE=$(MODE) CONFIRM=yes"; exit 1)
	$(RUN) db-reset --mode $(MODE) --yes

ingest: ## load the files present in data/raw/olist (validated, manifest, idempotent)
	$(RUN) ingest --mode $(MODE)

transform: ## staging -> core -> marts in one transaction, blocked by critical assertions
	$(RUN) transform --mode $(MODE)

quality: ## data-quality report and coverage audit (used to freeze the splits)
	$(RUN) quality --mode $(MODE)

features: ## point-in-time feature matrix (SQL)
	$(RUN) features --mode $(MODE)

train: ## selection, refit and calibration under the frozen temporal protocol (never reads the test block)
	$(RUN) train --mode $(MODE)

evaluate: ## final test evaluation of the latest trained run (re-runs are logged)
	$(RUN) evaluate --mode $(MODE)

benchmark: ## SQL benchmark in the isolated `bench` schema of MODE
	$(RUN) benchmark --mode $(MODE)

dashboard: ## run the Dash application on http://127.0.0.1:8050
	$(RUN) dashboard --mode $(MODE)

dashboard-docker: ## run the dashboard in Docker (DR_MODE taken from MODE)
	DR_MODE=$(MODE) $(COMPOSE) --profile app up --build app

test: ## all tests: unit, integration, temporal and dashboard (PostgreSQL required for most)
	$(UV) run pytest

test-fast: ## unit tests only (no database)
	$(UV) run pytest tests/unit

lint: ## Ruff lint and format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## apply Ruff formatting
	$(UV) run ruff format .

report: ## figures, evaluation report, executive summary, slides and CV bullets from saved artifacts
	$(RUN) report --mode $(MODE)

final-report: ## final PDF report in reports/final/ (needs tectonic: brew install tectonic)
	$(RUN) final-report --mode $(MODE)

screenshots: ## capture dashboard screenshots at 1440 and 1024 px (dashboard must be running, Chrome needed)
	$(RUN) screenshots --mode $(MODE)

reproduce: ## real data end to end: ingest -> transform -> quality -> features -> train -> evaluate -> report
	$(RUN) reproduce
