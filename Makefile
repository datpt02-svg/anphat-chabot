.PHONY: dev dev-backend dev-frontend build lint typecheck test e2e

# Load variables from .env (if present) so dev-backend works out of the box.
# Lines starting with # and blank lines are ignored. `?=` in the body
# provides a fallback if .env doesn't set the variable.
ifneq (,$(wildcard .env))
include .env
export
endif

# Fallback defaults if not set in .env.
JWT_SECRET ?= test-dev-secret
COPILOTKIT_DEV_AUTH_BYPASS ?= true
CORS_ALLOWED_ORIGINS ?= http://localhost:3000,http://localhost:5173

dev-backend:
	JWT_SECRET=$(JWT_SECRET) COPILOTKIT_DEV_AUTH_BYPASS=$(COPILOTKIT_DEV_AUTH_BYPASS) \
	CORS_ALLOWED_ORIGINS=$(CORS_ALLOWED_ORIGINS) \
	uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend:
	cd web && pnpm dev

dev:
	@echo "Run in 2 terminals:"
	@echo "  make dev-backend"
	@echo "  make dev-frontend"

build:
	cd web && pnpm build

lint:
	cd web && pnpm lint

typecheck:
	cd web && pnpm typecheck

test:
	uv run pytest -q

e2e:
	cd web && pnpm test:e2e
