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
	@test -n "$(PASSWORD)" || (echo "Usage: make reset-admin PASSWORD='new-password'" && exit 1)
	docker compose exec backend python -m app.reset_admin --password "$(PASSWORD)"

backup:
	./scripts/backup.sh
