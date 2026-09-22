# Частые команды проекта одним словом. `make` без цели печатает список.
#
# Переменные можно переопределять: make test UV=~/.local/bin/uv, make night MINUTES=10.
UV ?= uv
COMPOSE ?= docker compose
REGION ?= all
MINUTES ?= 120
OSRM_URL ?= http://localhost:5050

.DEFAULT_GOAL := help
.PHONY: help up rebuild down logs ps smoke test test-fast lint fmt front check bundles night transit transit-dry transit-error graph

help:  ## показать этот список
	@grep -hE '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | expand -t 16

## --- сервис ---

up:  ## собрать и поднять всё (backend, frontend, OSRM)
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

smoke:  ## проверить живой сервис: регионы, план дня, ночной план
	@curl -sf http://localhost:8000/api/config | python3 -c "import json,sys; d=json.load(sys.stdin); print('помощник:', d['llm_enabled'], '| слотов окон:', len(d.get('window_grid', [])))"
	@curl -sf http://localhost:8000/api/scenarios | python3 -c "import json,sys; [print(' ', s['title'], s['requests'], 'заявок', '(сгенерирован)' if s['generated'] else '') for s in json.load(sys.stdin)]"
	@bash scripts/smoke_day.sh

## --- проверки ---

test:  ## полный прогон бэкенда (~6.5 мин)
	cd backend && $(UV) run pytest -o addopts= -q

test-fast:  ## бэкенд без тестов солвера и API (быстрая обратная связь)
	cd backend && $(UV) run pytest -o addopts= -q --ignore=tests/test_api.py --ignore=tests/test_timeline_api.py

lint:  ## ruff: проверка стиля и форматирования
	cd backend && $(UV) run ruff check app tests scripts && $(UV) run ruff format --check app tests scripts

fmt:  ## ruff: отформатировать
	cd backend && $(UV) run ruff format app tests scripts

front:  ## фронт: тесты, типы, сборка
	cd frontend && npx vitest run && npx tsc --noEmit && npm run build

check: lint test front  ## всё сразу: то, что гоняется перед коммитом

## --- данные ---

bundles:  ## пересобрать бандлы регионов (нужен OSRM)
	cd backend && OSRM_URL=$(OSRM_URL) $(UV) run python -m app.synth.prepare --region $(REGION) --geocoder cache-only

night:  ## ночной поиск утренних планов: make night MINUTES=120 REGION=all
	cd backend && caffeinate -i $(UV) run python -m scripts.night_plan --region $(REGION) --minutes $(MINUTES)

transit-dry:  ## сколько запросов демо-ключа 2ГИС стоит пересчёт матриц
	cd backend && $(UV) run python -m scripts.transit_matrix --region $(REGION) --dry-run

transit:  ## пересчитать матрицы 2ГИС (нужен TWOGIS_API_KEY в .env; после — make rebuild)
	cd backend && $(UV) run python -m scripts.transit_matrix --region $(REGION)

transit-error:  ## ошибка встроенной формулы против матриц 2ГИС
	cd backend && $(UV) run python -m scripts.transit_error
