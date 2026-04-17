.PHONY: help install install-dev local up down dev prod logs ps shell \
        run lint format typecheck test test-cov migrate migrate-docker makemigration \
        downgrade reset-db pre-commit clean

PYTHON ?= python3
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
PY     := $(VENV)/bin/python
APP    := src.api.app:app

help:
	@echo "Available targets:"
	@echo "  install        Install production requirements into the local virtualenv"
	@echo "  install-dev    Install dev requirements and pre-commit hooks"
	@echo "  local          Start a local Postgres container for IDE-driven development"
	@echo "  up / dev       Run the staging-like stack (image only, points at dev RDS via .env)"
	@echo "  down           Stop and remove dev containers"
	@echo "  prod           Build and run the prod-style stack locally (points at prod RDS via .env)"
	@echo "  logs / ps      Tail or list dev containers"
	@echo "  shell          Open a shell inside the running app container"
	@echo "  run            Run the app locally with uvicorn (no Docker)"
	@echo "  lint           Run ruff linter"
	@echo "  format         Apply ruff formatter"
	@echo "  typecheck      Run mypy"
	@echo "  test           Run pytest"
	@echo "  test-cov       Run pytest with coverage report"
	@echo "  migrate        Apply latest Alembic migrations (via local venv)"
	@echo "  migrate-docker Apply migrations via Docker (builds image, runs against local Postgres)"
	@echo "  makemigration  Generate a new Alembic revision (use MSG=...)"
	@echo "  downgrade      Roll back one Alembic revision"
	@echo "  reset-db       Drop volumes and re-run migrations"
	@echo "  pre-commit     Run all pre-commit hooks against the repo"
	@echo "  clean          Remove caches and build artefacts"

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

install: $(VENV)/bin/activate
	$(PIP) install -r requirements/prod.txt

install-dev: $(VENV)/bin/activate
	$(PIP) install -r requirements/dev.txt
	$(VENV)/bin/pre-commit install

local:
	docker compose -f docker-compose-local.yaml up -d

up dev:
	docker compose -f docker-compose-dev.yaml up --build

down:
	docker compose -f docker-compose-dev.yaml down

prod:
	docker compose -f docker-compose-prod.yaml up --build

logs:
	docker compose -f docker-compose-dev.yaml logs -f

ps:
	docker compose -f docker-compose-dev.yaml ps

shell:
	docker compose -f docker-compose-dev.yaml exec app bash

run:
	$(VENV)/bin/uvicorn $(APP) --host 0.0.0.0 --port 8000 --reload

lint:
	$(VENV)/bin/ruff check src tests

format:
	$(VENV)/bin/ruff format src tests
	$(VENV)/bin/ruff check src tests --fix

typecheck:
	$(VENV)/bin/mypy src

test:
	$(VENV)/bin/pytest

test-cov:
	$(VENV)/bin/pytest --cov=src --cov-report=term-missing --cov-report=html

migrate:
	$(VENV)/bin/alembic upgrade head

migrate-docker:
	docker compose -f docker-compose-local.yaml up -d postgres
	docker compose -f docker-compose-local.yaml --profile migrate run --rm --build migrate

makemigration:
	@if [ -z "$(MSG)" ]; then echo "Usage: make makemigration MSG='your message'"; exit 1; fi
	$(VENV)/bin/alembic revision --autogenerate -m "$(MSG)"

downgrade:
	$(VENV)/bin/alembic downgrade -1

reset-db:
	docker compose -f docker-compose-local.yaml down -v
	docker compose -f docker-compose-local.yaml up -d
	sleep 3
	$(MAKE) migrate

pre-commit:
	$(VENV)/bin/pre-commit run --all-files

e2e:
	@if [ -z "$(REPO)" ] || [ -z "$(PROMPT)" ]; then \
		echo "Usage: make e2e REPO=https://github.com/owner/repo PROMPT='Fix the bug'"; \
		exit 1; \
	fi
	$(PY) scripts/e2e_test.py --repo "$(REPO)" --prompt "$(PROMPT)"

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
