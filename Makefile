# Частые команды проекта одним словом. `make` без цели печатает список.
#
# Переменные можно переопределять: make test UV=~/.local/bin/uv, make night MINUTES=10.
UV ?= uv
COMPOSE ?= docker compose
REGION ?= all
MINUTES ?= 120
OSRM_URL ?= http://localhost:5050
# Учётные данные базы дня: те же значения по умолчанию, что подставляет docker-compose.yml.
POSTGRES_USER ?= routing
POSTGRES_DB ?= routing
# Отдельная база для тестов, которые ходят в Postgres: поднимается на время прогона и убирается за собой.
PG_TEST_PORT ?= 55432
TEST_DATABASE_URL ?= postgresql://routing:routing@localhost:$(PG_TEST_PORT)/routing
PG_TEST_NAME ?= routing-test-db

.DEFAULT_GOAL := help
.PHONY: help up rebuild down logs ps smoke db migrate rollback psql test test-fast test-db lint typecheck fmt front check \
	pre-commit-install bundles night transit transit-dry transit-error graph

help:  ## показать этот список
	@grep -hE '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | expand -t 20

## --- сервис ---

up:  ## собрать и поднять всё (backend, frontend, OSRM, Postgres)
	$(COMPOSE) up -d --build

rebuild:  ## пересобрать только backend: бандлы, ночные планы и матрицы едут в образе
	$(COMPOSE) up -d --build backend

down:  ## остановить контейнеры
	$(COMPOSE) down

logs:  ## хвост лога backend
	$(COMPOSE) logs -f --tail 100 backend

ps:  ## что запущено
	$(COMPOSE) ps

graph:  ## построить граф дорог OSRM (один раз, ~3.5 мин)
	$(COMPOSE) --profile prepare run --rm --build osrm-prepare

## --- база дня ---

db:  ## поднять только Postgres, в котором живёт день диспетчера
	$(COMPOSE) up -d postgres

migrate:  ## применить миграции схемы вручную; обычно backend делает это сам на старте
	$(COMPOSE) exec backend python -m app.state.migrate

rollback:  ## откатить последнюю миграцию; вместе с таблицами уходят все сохранённые дни
	$(COMPOSE) exec backend python -m app.state.migrate rollback

psql:  ## заглянуть в базу дня: psql внутри её контейнера
	$(COMPOSE) exec postgres psql -U $(POSTGRES_USER) $(POSTGRES_DB)

smoke:  ## проверить живой сервис: регионы, план дня, ночной план
	@curl -sf http://localhost:8000/api/config | python3 -c "import json,sys; d=json.load(sys.stdin); print('помощник:', d['llm_enabled'], '| слотов окон:', len(d.get('window_grid', [])))"
	@curl -sf http://localhost:8000/api/scenarios | python3 -c "import json,sys; [print(' ', s['title'], s['requests'], 'заявок', '(сгенерирован)' if s['generated'] else '') for s in json.load(sys.stdin)]"
	@bash scripts/smoke_day.sh

## --- проверки ---

test:  ## полный прогон бэкенда без базы (~6.5 мин): половины [postgres] уходят в skipped
	cd backend && $(UV) run pytest -o addopts= -q
	@echo "Тесты базы (маркер db) пропущены — зелёный прогон тут не полный. Прогнать их: make test-db"

test-fast:  ## бэкенд без тестов солвера и API (быстрая обратная связь)
	cd backend && $(UV) run pytest -o addopts= -q --ignore=tests/test_api.py --ignore=tests/test_timeline_api.py

test-db:  ## всё, что ходит в Postgres (маркер db): своя база на PG_TEST_PORT, убирается за собой
	@docker rm -f $(PG_TEST_NAME) >/dev/null 2>&1 || true
	@docker run -d --name $(PG_TEST_NAME) -e POSTGRES_DB=routing -e POSTGRES_USER=routing \
		-e POSTGRES_PASSWORD=routing -p $(PG_TEST_PORT):5432 postgres:17-alpine >/dev/null
	@until docker exec $(PG_TEST_NAME) pg_isready -U routing -d routing >/dev/null 2>&1; do sleep 1; done
	@cd backend && TEST_DATABASE_URL=$(TEST_DATABASE_URL) $(UV) run pytest -o addopts= -q -m db; status=$$?; \
		docker rm -f $(PG_TEST_NAME) >/dev/null; exit $$status

lint:  ## ruff: проверка стиля и форматирования
	cd backend && $(UV) run ruff check app tests scripts && $(UV) run ruff format --check app tests scripts

typecheck:  ## mypy: типы бэкенда (app, scripts, tests; настройки в backend/pyproject.toml)
	cd backend && $(UV) run mypy

fmt:  ## ruff: отформатировать
	cd backend && $(UV) run ruff format app tests scripts

front:  ## фронт: тесты, типы, сборка
	cd frontend && npx vitest run && npx tsc --noEmit && npm run build

check: lint typecheck test front  ## всё сразу: то, что гоняется перед коммитом

# pre-commit — отдельный инструмент uv (uv tool): его окружение живёт вне проекта, и путь к нему, записанный в хук,
# не пропадёт при чистке кэша uv, как пропал бы у разового uvx. Хук один на репозиторий, общий для всех его
# git worktree; --allow-missing-config — чтобы на ветках без .pre-commit-config.yaml коммит не падал, а шёл без проверок.
pre-commit-install:  ## git-хук на коммит: ruff, mypy и проверки файлов из .pre-commit-config.yaml
	$(UV) tool install --quiet pre-commit
	$(UV) tool run pre-commit install --allow-missing-config

## --- данные ---

bundles:  ## пересобрать бандлы регионов (нужен OSRM)
	cd backend && OSRM_URL=$(OSRM_URL) $(UV) run python -m app.synth.prepare --region $(REGION) --geocoder cache-only

# Долгий расчёт не должен прерываться сном машины: caffeinate есть только на macOS, на остальных системах пусто.
KEEP_AWAKE := $(shell command -v caffeinate >/dev/null 2>&1 && echo caffeinate -i)

night:  ## ночной поиск утренних планов: make night MINUTES=120 REGION=all
	cd backend && $(KEEP_AWAKE) $(UV) run python -m scripts.night_plan --region $(REGION) --minutes $(MINUTES)

transit-dry:  ## сколько запросов демо-ключа 2ГИС стоит пересчёт матриц
	cd backend && $(UV) run python -m scripts.transit_matrix --region $(REGION) --dry-run

transit:  ## пересчитать матрицы 2ГИС (нужен TWOGIS_API_KEY в .env; после — make rebuild)
	cd backend && $(UV) run python -m scripts.transit_matrix --region $(REGION)

transit-error:  ## ошибка встроенной формулы против матриц 2ГИС
	cd backend && $(UV) run python -m scripts.transit_error
