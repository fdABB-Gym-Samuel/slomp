## Slomp project Makefile.
## Run `make` or `make help` for a list of targets.
##
## Most database commands rely on env vars exported by the nix dev shell
## (PGDATA, PGHOST, PGPORT, PGDATABASE). The `?=` assignments below act as
## fallbacks if you run make outside `direnv`.

PGDATA     ?= .pg
PGHOST     ?= /tmp
PGPORT     ?= 5432
PGDATABASE ?= slomp
PGUSER     ?= $(USER)

VALKEY_DATA ?= .valkey
VALKEY_PORT ?= 6379

DB_URL := postgres://$(PGUSER)@localhost:$(PGPORT)/$(PGDATABASE)?sslmode=disable
DBMATE := DATABASE_URL="$(DB_URL)" dbmate --migrations-dir migrations --no-dump-schema

.DEFAULT_GOAL := help

# ---------- help ----------------------------------------------------------

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} \
	     /^# ----/ {sub(/^# -+ */, ""); sub(/ *-+$$/, ""); printf "\n\033[1m%s\033[0m\n", $$0; next} \
	     /^[a-zA-Z0-9_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' \
	     $(MAKEFILE_LIST)

# ---------- lifecycle (setup / kill / clean) ------------------------------

.PHONY: setup setup-backend setup-frontend kill clean

setup: setup-backend setup-frontend ## First-time bootstrap — services + frontend deps

setup-backend: db-up db-create migrate-up valkey-up ## Start Postgres, run migrations, start Valkey

setup-frontend: ## Install frontend dependencies (bun install)
	cd frontend && bun install

kill: ## Stop all background services (Postgres + Valkey)
	-@$(MAKE) --no-print-directory db-down
	-@$(MAKE) --no-print-directory valkey-down

clean: ## Stop services and wipe ALL derived state (caches, data dirs, node_modules) — DESTRUCTIVE
	@printf "\033[33mThis will wipe Postgres data, Valkey data, all caches, and node_modules. Continue?\033[0m [y/N] "; \
	read REPLY; \
	if [ "$$REPLY" != "y" ] && [ "$$REPLY" != "Y" ]; then echo "aborted"; exit 1; fi
	@$(MAKE) --no-print-directory kill
	find . -type d \( -name __pycache__ -o -name .ruff_cache -o -name .pytest_cache -o -name .svelte-kit -o -name .vite \) -prune -exec rm -rf {} +
	rm -f docs/openapi.yaml
	rm -rf frontend/build frontend/node_modules
	rm -rf $(PGDATA) $(VALKEY_DATA)
	@echo "done."

# ---------- database ------------------------------------------------------

.PHONY: db-init db-up db-down db-create db-drop db-reset db-shell

db-init: ## Initialize the local Postgres data directory
	@test -d $(PGDATA) || initdb -D $(PGDATA) -U $(PGUSER) --auth=trust --no-locale --encoding=UTF8

db-up: db-init ## Start the local Postgres server
	pg_ctl -D $(PGDATA) -l $(PGDATA)/postgres.log -o "-p $(PGPORT) -k $(PGHOST)" start

db-down: ## Stop the local Postgres server
	pg_ctl -D $(PGDATA) stop

db-create: ## Create the slomp database
	createdb $(PGDATABASE)

db-drop: ## Drop the slomp database
	dropdb --if-exists $(PGDATABASE)

db-reset: db-drop db-create migrate-up ## Drop, recreate, and re-migrate

db-shell: ## Open a psql shell on the slomp database
	psql

# ---------- valkey --------------------------------------------------------

.PHONY: valkey-up valkey-down valkey-shell

valkey-up: ## Start the local Valkey server
	@mkdir -p $(VALKEY_DATA)
	valkey-server --port $(VALKEY_PORT) --bind 127.0.0.1 --dir $(VALKEY_DATA) --daemonize yes --pidfile $(PWD)/$(VALKEY_DATA)/valkey.pid --logfile $(PWD)/$(VALKEY_DATA)/valkey.log

valkey-down: ## Stop the local Valkey server
	-valkey-cli -p $(VALKEY_PORT) shutdown nosave

valkey-shell: ## Open a valkey-cli shell
	valkey-cli -p $(VALKEY_PORT)

# ---------- migrations ----------------------------------------------------

.PHONY: migrate-up migrate-down migrate-create migrate-status

migrate-up: ## Apply all pending migrations
	$(DBMATE) up

migrate-down: ## Roll back the last migration
	$(DBMATE) down

migrate-create: ## Create a new migration (NAME=description)
	@test -n "$(NAME)" || { echo "usage: make migrate-create NAME=add_something"; exit 1; }
	$(DBMATE) new $(NAME)

migrate-status: ## Show migration status
	$(DBMATE) status

# ---------- backend -------------------------------------------------------

.PHONY: dev backend-dev test

dev: ## Run backend AND frontend together (Ctrl-C kills both)
	@test -d backend || { echo "backend/ does not exist yet — scaffold it first"; exit 1; }
	@test -d frontend/node_modules || { echo "frontend deps missing — run 'make setup-frontend'"; exit 1; }
	@trap 'trap - INT TERM; kill -INT 0 2>/dev/null; wait 2>/dev/null; exit 0' INT TERM; \
	  ( cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 2>&1 | sed -u 's/^/[backend]  /' ) & \
	  ( cd frontend && bun run dev 2>&1 | sed -u 's/^/[frontend] /' ) & \
	  wait || true

backend-dev: ## Run only the backend on http://localhost:8000
	@test -d backend || { echo "backend/ does not exist yet — scaffold it first"; exit 1; }
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test: ## Run backend tests
	@test -d backend || { echo "backend/ does not exist yet — scaffold it first"; exit 1; }
	cd backend && pytest

# ---------- frontend ------------------------------------------------------

.PHONY: frontend-dev frontend-build frontend-check

frontend-dev: ## Run SvelteKit dev server on http://localhost:5173
	@test -d frontend/node_modules || { echo "node_modules missing — run 'make setup-frontend'"; exit 1; }
	cd frontend && bun run dev

frontend-build: ## Build the SvelteKit production bundle
	cd frontend && bun run build

frontend-check: ## Type-check the frontend
	cd frontend && bun run check

# ---------- API docs ------------------------------------------------------

.PHONY: docs

docs/openapi.yaml: openapi.yaml
	@cp $< $@

docs: docs/openapi.yaml ## Serve Swagger UI for openapi.yaml on http://localhost:8001
	@echo "Open http://localhost:8001/"
	@cd docs && python -m http.server 8001

# ---------- quality -------------------------------------------------------

.PHONY: fmt check

fmt: ## Format the entire repo via treefmt
	nix fmt

check: ## Run nix flake check (formatting + builds)
	nix flake check

# ---------- build ---------------------------------------------------------

.PHONY: build

build: ## Build the frontend production bundle (backend has no build step)
	cd frontend && bun run build
