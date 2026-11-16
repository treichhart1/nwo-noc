.PHONY: up down logs ps build validate reset-admin backup

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

build:
	docker compose build

validate:
	./scripts/validate.sh

reset-admin:
	@echo "Use the approved local recovery runbook; never supply PASSWORD in make arguments."
	@exit 1

backup:
	./scripts/backup.sh
