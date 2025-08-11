COMPOSE        ?= docker compose
COMPOSE_FILE   ?= docker-compose.yml
SERVICE        ?= chatbot           # service name in compose
CONTAINER      ?= chatbot           # container_name in compose
ENV_FILE       ?= .env

.PHONY: help check-env build up down restart logs ps shell exec rebuild nuke prune image-id port raw-tree

help:
	@echo ""
	@echo "Targets:"
	@echo "  make build        - Build images"
	@echo "  make up           - Start stack in background"
	@echo "  make down         - Stop stack (keeps volumes)"
	@echo "  make restart      - Recreate containers"
	@echo "  make logs         - Follow app logs"
	@echo "  make ps           - Show running containers"
	@echo "  make shell        - Interactive shell inside app container"
	@echo "  make exec CMD='…' - Run arbitrary command in app container"
	@echo "  make rebuild      - Rebuild without cache and recreate"
	@echo "  make prune        - Prune unused Docker data (safe-ish)"
	@echo "  make nuke         - Down + remove volumes (DANGER: deletes DB volume)"
	@echo "  make image-id     - Print image id used by the service"
	@echo "  make port         - Show published ports for the container"
	@echo "  make raw-tree     - List /app contents from the raw image (no mounts)"
	@echo ""

check-env:
	@test -f $(ENV_FILE) || (echo "Missing $(ENV_FILE). Copy .env.example to .env and fill it."; exit 1)

env: ## Create .env from example if missing
	@test -f .env || cp .env.example .env

build: check-env
	$(COMPOSE) -f $(COMPOSE_FILE) build

up: build
	$(COMPOSE) -f $(COMPOSE_FILE) up -d
	@$(COMPOSE) -f $(COMPOSE_FILE) ps

down:
	$(COMPOSE) -f $(COMPOSE_FILE) down --remove-orphans

restart:
	$(COMPOSE) -f $(COMPOSE_FILE) down --remove-orphans
	$(COMPOSE) -f $(COMPOSE_FILE) up -d

logs:
	$(COMPOSE) -f $(COMPOSE_FILE) logs -f

ps:
	$(COMPOSE) -f $(COMPOSE_FILE) ps

shell:
	@$(COMPOSE) -f $(COMPOSE_FILE) exec $(SERVICE) bash -l 2>/dev/null || \
	 $(COMPOSE) -f $(COMPOSE_FILE) exec $(SERVICE) sh

# usage: make exec CMD="ls -la /app"
exec:
	@test -n "$(CMD)" || (echo "Usage: make exec CMD=\"<command>\""; exit 1)
	@$(COMPOSE) -f $(COMPOSE_FILE) exec $(SERVICE) sh -lc '$(CMD)'

rebuild:
	$(COMPOSE) -f $(COMPOSE_FILE) build --no-cache
	$(COMPOSE) -f $(COMPOSE_FILE) up -d --force-recreate

# Careful: removes EVERYTHING unused (images, networks, cache)
prune:
	docker system prune -f

# DANGER: also removes volumes (SQLite file if stored in a named volume)
nuke:
	$(COMPOSE) -f $(COMPOSE_FILE) down -v --remove-orphans

image-id:
	@$(COMPOSE) -f $(COMPOSE_FILE) images -q $(SERVICE)

port:
	@docker port $(CONTAINER) || (echo "Container $(CONTAINER) not found (is it running?)"; exit 1)

# Inspect the raw image filesystem (no host mounts). Requires a built image.
raw-tree:
	@IMG_ID=`$(COMPOSE) -f $(COMPOSE_FILE) images -q $(SERVICE)`; \
	 test -n "$$IMG_ID" || (echo "No image built yet. Run 'make build'."; exit 1); \
	 docker run --rm --entrypoint sh $$IMG_ID -lc 'echo \"== /app ==\"; ls -la /app; echo; echo \"== /app/app ==\"; ls -la /app/app'
