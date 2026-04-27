.PHONY: help up down restart rebuild logs ps shell psql \
        migrate makemigration downgrade reset-db \
        lint format typecheck test test-cov pre-commit \
        dev prod clean

COMPOSE_LOCAL := docker compose -f docker-compose-local.yaml
COMPOSE_DEV   := docker compose -f docker-compose-dev.yaml
COMPOSE_PROD  := docker compose -f docker-compose-prod.yaml
APP_SVC       := app

help:
	@echo "Local development (everything runs in Docker — no host venv needed):"
	@echo "  up             Build and start the full local stack (postgres + migrations + app with hot reload)"
	@echo "  down           Stop and remove local containers"
	@echo "  restart        Restart the app container"
	@echo "  rebuild        Rebuild images and restart"
	@echo "  logs           Tail logs from all local services"
	@echo "  ps             List local containers"
	@echo "  shell          Open a bash shell inside the running app container"
	@echo "  psql           Open a psql prompt inside the postgres container"
	@echo ""
	@echo "Database:"
	@echo "  migrate        Apply latest Alembic migrations"
	@echo "  makemigration  Generate a new Alembic revision (use MSG=...)"
	@echo "  downgrade      Roll back one Alembic revision"
	@echo "  reset-db       Drop volumes and re-run migrations from scratch"
	@echo ""
	@echo "Quality (runs inside the app container):"
	@echo "  lint           Run ruff linter"
	@echo "  format         Apply ruff formatter and fix lint issues"
	@echo "  typecheck      Run mypy"
	@echo "  test           Run pytest"
	@echo "  test-cov       Run pytest with coverage report"
	@echo "  pre-commit     Run all pre-commit hooks against the repo"
	@echo ""
	@echo "Other environments:"
	@echo "  dev            Run the staging-like stack (points at dev RDS via .env)"
	@echo "  prod           Run the prod-style stack locally (points at prod RDS via .env)"
	@echo "  clean          Remove caches and build artefacts"

up:
	$(COMPOSE_LOCAL) up --build -d
	$(COMPOSE_LOCAL) logs -f $(APP_SVC)

down:
	$(COMPOSE_LOCAL) down

restart:
	$(COMPOSE_LOCAL) restart $(APP_SVC)

rebuild:
	$(COMPOSE_LOCAL) up --build -d --force-recreate

logs:
	$(COMPOSE_LOCAL) logs -f

ps:
	$(COMPOSE_LOCAL) ps

shell:
	$(COMPOSE_LOCAL) exec $(APP_SVC) bash

psql:
	$(COMPOSE_LOCAL) exec postgres psql -U $${POSTGRES_USER:-clyde} -d $${POSTGRES_DB:-clyde}

migrate:
	$(COMPOSE_LOCAL) run --rm migrate

makemigration:
	@if [ -z "$(MSG)" ]; then echo "Usage: make makemigration MSG='your message'"; exit 1; fi
	$(COMPOSE_LOCAL) run --rm migrate alembic revision --autogenerate -m "$(MSG)"

downgrade:
	$(COMPOSE_LOCAL) run --rm migrate alembic downgrade -1

reset-db:
	$(COMPOSE_LOCAL) down -v
	$(COMPOSE_LOCAL) up --build -d

lint:
	$(COMPOSE_LOCAL) exec $(APP_SVC) ruff check src tests

format:
	$(COMPOSE_LOCAL) exec $(APP_SVC) ruff format src tests
	$(COMPOSE_LOCAL) exec $(APP_SVC) ruff check src tests --fix

typecheck:
	$(COMPOSE_LOCAL) exec $(APP_SVC) mypy src

test:
	$(COMPOSE_LOCAL) exec $(APP_SVC) pytest

test-cov:
	$(COMPOSE_LOCAL) exec $(APP_SVC) pytest --cov=src --cov-report=term-missing --cov-report=html

pre-commit:
	$(COMPOSE_LOCAL) exec $(APP_SVC) pre-commit run --all-files

dev:
	$(COMPOSE_DEV) up --build

prod:
	$(COMPOSE_PROD) up --build

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
