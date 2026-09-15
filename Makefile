# trajectory: evaluation harness for coding agents
#
# Every target here is what CI runs, so a green `make check` locally means a green pipeline.

SHELL := /bin/bash
.DEFAULT_GOAL := help
UV ?= uv
PY := $(UV) run

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

## ----------------------------------------------------------------- setup

.PHONY: install
install: ## Create the virtualenv and install every workspace package
	$(UV) sync --all-packages --all-extras
	$(PY) pre-commit install

.PHONY: web-install
web-install: ## Install web app dependencies
	cd web && npm ci

## ----------------------------------------------------------------- quality

.PHONY: lint
lint: ## ruff check and format check
	$(PY) ruff check .
	$(PY) ruff format --check .

.PHONY: fmt
fmt: ## Apply ruff formatting and autofixes
	$(PY) ruff check --fix .
	$(PY) ruff format .

.PHONY: typecheck
typecheck: ## mypy strict across all three packages
	$(PY) mypy

.PHONY: no-em-dash
no-em-dash: ## Fail if an em dash appears anywhere in the repository
	$(PY) python scripts/check_no_em_dash.py

.PHONY: numbers
numbers: ## Fail if a published figure does not match the committed run records
	$(PY) python scripts/check_published_numbers.py

.PHONY: local-backend
local-backend: ## Report whether a local backend task can run its verify command here
	$(PY) python scripts/check_local_backend.py

.PHONY: machine-paths
machine-paths: ## Fail if a tracked file hardcodes a path from one machine
	$(PY) python scripts/check_no_machine_paths.py

.PHONY: container-paths
container-paths: ## Fail if a multi-stage Dockerfile moves an editable virtualenv
	$(PY) python scripts/check_container_paths.py

.PHONY: line-endings
line-endings: ## Fail if a tracked text file carries a carriage return
	$(PY) python scripts/check_line_endings.py

.PHONY: test
test: ## Run the test suite with the coverage floor
	$(PY) pytest --cov --cov-report=term-missing --cov-report=xml --cov-fail-under=80

.PHONY: test-fast
test-fast: ## Run the test suite without coverage or slow markers
	$(PY) pytest -m "not slow and not docker and not postgres" -q

.PHONY: check
check: lint typecheck no-em-dash numbers line-endings container-paths machine-paths test tasks-validate ## Everything CI runs

## ----------------------------------------------------------------- harness

.PHONY: tasks-validate
tasks-validate: ## Lint every task definition in tasks/
	$(PY) trajectory tasks validate

.PHONY: tasks-references
tasks-references: ## Run every reference solution against its hidden tests
	$(PY) trajectory tasks verify-references --suite core-12

.PHONY: demo-suite
demo-suite: ## Run the whole suite against the offline reference policies
	$(PY) trajectory run --suite core-12 --model stub:methodical --out runs/demo

.PHONY: matrix
matrix: ## Rebuild the published measurement matrix from scratch
	$(PY) python scripts/run_matrix.py

## ----------------------------------------------------------------- services

.PHONY: db-up
db-up: ## Start local Postgres
	docker compose up -d db

.PHONY: db-migrate
db-migrate: ## Apply Alembic migrations to DATABASE_URL
	cd packages/api && $(PY) alembic upgrade head

.PHONY: api-dev
api-dev: ## Run the API with reload
	$(PY) uvicorn --factory trajectory_api.main:create_app --reload --port 8000

.PHONY: seed
seed: ## Load the recorded fixture runs into the database
	$(PY) python scripts/seed_db.py

.PHONY: web-dev
web-dev: ## Run the web app against the local API
	cd web && npm run dev

.PHONY: web-build
web-build: bundle ## Build the static demo from the committed fixtures
	cd web && npm run build

.PHONY: web-smoke
web-smoke: ## Playwright smoke suite against a built web app
	cd web && npx playwright test

## ----------------------------------------------------------------- misc

.PHONY: bundle
bundle: ## Rebuild the web demo data from the committed fixture runs
	$(PY) python scripts/build_web_bundle.py --from-fixtures

.PHONY: fixtures
fixtures: ## Re-record the committed fixture runs from the newest matrix run
	$(PY) python scripts/build_web_bundle.py runs/matrix --fixtures fixtures/recorded-runs

.PHONY: clean
clean: ## Remove build and cache artefacts
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov coverage.xml .coverage
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf web/.next web/out
