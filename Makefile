.DEFAULT_GOAL := help
SHELL := /bin/bash

UV ?= uv
COMPOSE ?= docker compose
API_DIR := services/api
TEST_DB_URL ?= postgresql+asyncpg://janus:janus@localhost:5432/janus_test
# Compose reads .env; Make does not. Load only the published-port keys so
# `make stack-up` prints the ports that are actually bound.
ifneq ($(wildcard .env),)
JANUS_API_PORT ?= $(shell awk -F= '/^JANUS_API_PORT=/{print $$2}' .env)
JANUS_WEB_PORT ?= $(shell awk -F= '/^JANUS_WEB_PORT=/{print $$2}' .env)
JANUS_GATEWAY_PORT ?= $(shell awk -F= '/^JANUS_GATEWAY_PORT=/{print $$2}' .env)
endif
JANUS_API_PORT ?= 8080
JANUS_WEB_PORT ?= 3000
JANUS_GATEWAY_PORT ?= 8081
# Next 16 and Vitest 4 need Node 20.12+ (`styleText` in node:util). CI uses 22.
# Prefer a user-local install when the distro Node is too old.
NODE22 := $(HOME)/.local/node-v22
ifneq ($(wildcard $(NODE22)/bin/node),)
export PATH := $(NODE22)/bin:$(PATH)
endif

.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------- setup

.PHONY: install
install: ## Install the Python workspace and web dependencies
	$(UV) sync
	cd apps/web && npm install

.PHONY: bootstrap
bootstrap: ## Install Terraform, AWS CLI, gh, Node 22, then workspace deps
	./install.sh

.PHONY: node
node: ## Install Node 22 locally (needed when the distro Node is older than 20.12)
	@mkdir -p "$(NODE22)"
	docker run --rm --user root -v "$(NODE22):/out" node:22-bookworm-slim \
		bash -c 'tar -C /usr/local -cf - bin/node bin/npm bin/npx bin/corepack lib include share | tar -C /out -xf -'
	@echo "Node $$("$(NODE22)/bin/node" -v) installed at $(NODE22)"
	@echo "make targets will use it automatically. For a shell: export PATH=\"$(NODE22)/bin:\$$PATH\""

.PHONY: env
env: ## Create .env from the example if it does not exist
	@test -f .env || (cp .env.example .env && echo "created .env")

# ------------------------------------------------------------- local stack

.PHONY: db-up
db-up: ## Start Postgres
	$(COMPOSE) up -d postgres
	@until $(COMPOSE) exec -T postgres pg_isready -U janus -d janus >/dev/null 2>&1; do sleep 1; done
	@echo "postgres ready"

.PHONY: db-down
db-down: ## Stop Postgres (keeps data)
	$(COMPOSE) stop postgres

.PHONY: db-reset
db-reset: ## Destroy and recreate the database volume
	$(COMPOSE) down -v postgres
	$(MAKE) db-up migrate

.PHONY: stack-up
stack-up: ## Start everything in containers (postgres, migrations, gateway, api, web)
	$(COMPOSE) --profile full up -d --build
	@echo "web  http://localhost:$(JANUS_WEB_PORT)"
	@echo "api  http://localhost:$(JANUS_API_PORT)"

.PHONY: stack-down
stack-down: ## Stop the full container stack
	$(COMPOSE) --profile full down

# -------------------------------------------------------------- migrations

.PHONY: migrate
migrate: ## Apply migrations
	cd $(API_DIR) && $(UV) run alembic upgrade head

.PHONY: migrate-down
migrate-down: ## Roll back one migration
	cd $(API_DIR) && $(UV) run alembic downgrade -1

.PHONY: migration
migration: ## Create a migration:  make migration m="add teams"
	cd $(API_DIR) && $(UV) run alembic revision --autogenerate -m "$(m)"

# -------------------------------------------------------------------- run

.PHONY: run-gateway
run-gateway: ## Run the Model Gateway on :8081
	$(UV) run uvicorn gateway_app.main:app --reload --port 8081 \
		--app-dir services/gateway

.PHONY: run-api
run-api: ## Run the control plane
	cd $(API_DIR) && $(UV) run uvicorn api_app.main:app --reload --port $(JANUS_API_PORT)

.PHONY: run-web
run-web: ## Run the web app
	cd apps/web && npm run dev -- --port $(JANUS_WEB_PORT)

.PHONY: smoke-chat
smoke-chat: ## Log in, create a conversation, stream one reply
	$(UV) run python scripts/smoke_chat.py

.PHONY: smoke-product
smoke-product: ## Knowledge, agent run, and /v1/responses against the running API
	$(UV) run python scripts/smoke_product.py

# ------------------------------------------------------------------- tests

.PHONY: test
test: test-db ## Run all tests (starts Postgres for the control-plane suite)
	JANUS_TEST_DATABASE_URL=$(TEST_DB_URL) $(UV) run pytest

.PHONY: test-gateway
test-gateway: ## Gateway tests only (no database needed)
	$(UV) run pytest services/gateway/tests -q

.PHONY: test-api
test-api: test-db ## Control plane tests (needs Postgres)
	JANUS_TEST_DATABASE_URL=$(TEST_DB_URL) $(UV) run pytest services/api/tests -q

.PHONY: test-db
test-db: db-up ## Create the test database
	@$(COMPOSE) exec -T postgres psql -U janus -d postgres \
		-c "SELECT 1 FROM pg_database WHERE datname='janus_test'" | grep -q 1 \
		|| $(COMPOSE) exec -T postgres createdb -U janus janus_test

.PHONY: test-conformance
test-conformance: ## Adapter conformance suite against a live Ollama
	JANUS_TEST_OLLAMA=1 $(UV) run pytest services/gateway/tests/conformance -q

# ------------------------------------------------------------------ checks

.PHONY: lint
lint: ## Lint
	$(UV) run ruff check .
	$(UV) run ruff format --check .

.PHONY: format
format: ## Format
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

.PHONY: typecheck
typecheck: ## Type check
	$(UV) run mypy packages services

.PHONY: boundaries
boundaries: ## Enforce architectural boundaries (ADR 0001)
	$(UV) run lint-imports

.PHONY: web-lint
web-lint: ## Lint the web app
	cd apps/web && npm run lint

.PHONY: web-typecheck
web-typecheck: ## Type check the web app
	cd apps/web && npm run typecheck

.PHONY: web-test
web-test: ## Web app tests
	@node -e "const [maj,min]=process.versions.node.split('.').map(Number); if (maj<20||(maj===20&&min<12)) { console.error('Node 20.12+ required (found '+process.version+'). Run: make node'); process.exit(1); }"
	cd apps/web && npm test

.PHONY: web-build
web-build: ## Production build of the web app
	cd apps/web && npm run build

.PHONY: check
check: lint typecheck boundaries web-lint web-typecheck web-test test ## Everything CI runs

# ------------------------------------------------------------------ images

.PHONY: images
images: ## Build container images
	docker build -f services/gateway/Dockerfile -t janus/gateway:dev .
	docker build -f services/api/Dockerfile -t janus/api:dev .
	docker build -t janus/web:dev apps/web
