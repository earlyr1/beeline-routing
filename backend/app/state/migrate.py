"""Миграции схемы: yoyo и чистый SQL из backend/migrations.

Запускаются в backend на старте, до того как uvicorn начнёт отвечать. Отдельным сервисом compose их делать
нельзя: `docker compose restart backend` обязан приводить схему в порядок сам, а one-shot сервис при таком
перезапуске не стартует. Ошибка миграции поднимает исключение — сервис не запускается, healthcheck не зеленеет,
и это громко и правильно: тихо уехать в память нельзя, диспетчер будет думать, что день сохраняется.

Файлы: `<номер>.<что-меняем>.sql` и рядом `<то же>.rollback.sql`. Порядок — по имени, поэтому числовой префикс;
зависимость объявляется строкой `-- depends: 0001.day-state` в начале файла. Каждая миграция идёт одной
транзакцией: DDL в Postgres транзакционный, и половины миграции не остаётся.

Вручную тем же набором файлов: `make migrate` (или `yoyo apply --batch --database … ./migrations`).
Обратный ход — `make rollback` (`python -m app.state.migrate rollback`): он откатывает последнюю
применённую миграцию. Откат 0001 сносит таблицы, а вместе с ними все сохранённые дни; откат 0002 (ключи шагов
шкалы) ничего не делает; откат 0003 возвращает пустую таблицу прежних отметок звонков agreed_windows.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Sequence

from yoyo import get_backend, read_migrations

from app.settings import BACKEND_DIR

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = BACKEND_DIR / "migrations"
# Сколько раз ждём базу: `depends_on: service_healthy` уже её дождался, но при одиночном `up backend`
# postgres бывает здоров и ещё не принимает соединения.
CONNECT_RETRIES = 5
CONNECT_PAUSE_S = 1.0


def yoyo_url(database_url: str) -> str:
    """DATABASE_URL в форме yoyo: драйвер он выбирает по схеме, и postgresql:// у него psycopg2.

    Переменная окружения одна и в форме psycopg (postgresql://…), иначе на ровном месте «ImportError: psycopg2».
    """
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    return database_url


def migrate(database_url: str, *, retries: int = CONNECT_RETRIES, pause_s: float = CONNECT_PAUSE_S) -> None:
    """Применяет миграции backend/migrations. На актуальной базе не делает ничего."""
    backend = _connect(yoyo_url(database_url), retries, pause_s)
    migrations = read_migrations(str(MIGRATIONS_DIR))
    # Блокировка yoyo: два backend'а, стартовавшие одновременно, не перетопчут друг друга.
    with backend.lock():
        pending = backend.to_apply(migrations)
        if not pending:
            logger.info("Миграции: применять нечего, база актуальна")
            return
        logger.info("Миграции: применяем %d", len(pending))
        backend.apply_migrations(pending)


def rollback(database_url: str, *, retries: int = CONNECT_RETRIES, pause_s: float = CONNECT_PAUSE_S) -> None:
    """Откатывает последнюю применённую миграцию. Что при этом теряется, сказано в её .rollback.sql: откат 0001
    уносит таблицы вместе со всеми сохранёнными днями."""
    backend = _connect(yoyo_url(database_url), retries, pause_s)
    migrations = read_migrations(str(MIGRATIONS_DIR))
    with backend.lock():
        applied = backend.to_rollback(migrations)
        if not applied:
            logger.info("Миграции: откатывать нечего, база пустая")
            return
        last = applied[:1]
        logger.info("Миграции: откатываем %s (что теряется — в его .rollback.sql)", last[0].id)
        backend.rollback_migrations(last)


def _connect(url: str, retries: int, pause_s: float):
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return get_backend(url)
        except Exception as error:  # noqa: BLE001 - драйверы бросают своё, ждём базу одинаково
            last = error
            if attempt + 1 < retries:
                logger.info("База ещё не отвечает, ждём: %s", error)
                time.sleep(pause_s)
    raise RuntimeError(f"Не удалось подключиться к базе из DATABASE_URL: {last}") from last


def main(argv: Sequence[str] | None = None) -> None:
    """Ручной запуск теми же файлами: `python -m app.state.migrate [rollback]`.

    В compose это `make migrate` и `make rollback`. Без аргумента — применить, `rollback` — откатить
    последнюю миграцию вместе со всеми сохранёнными днями.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in ([], ["rollback"]):
        raise SystemExit("Использование: python -m app.state.migrate [rollback]")
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL не задан: миграции применять некуда.")
    if args == ["rollback"]:
        rollback(database_url)
    else:
        migrate(database_url)


if __name__ == "__main__":
    main()
