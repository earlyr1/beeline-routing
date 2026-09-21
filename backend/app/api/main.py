"""Точка входа uvicorn: uvicorn app.api.main:app"""

import logging

from app.api.app import create_app


def _log_app_to_stderr() -> None:
    """Строки логгеров app.* в лог контейнера, начиная с INFO.

    uvicorn настраивает только свои логгеры, а без обработчика Python печатает лишь WARNING и выше. Так терялись
    бы строки, почему ночной план взят утренним или нет (app/planning/night.py): docs/demo.md ищет их в
    `docker compose logs backend`.
    """
    logger = logging.getLogger("app")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:  %(name)s: %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


_log_app_to_stderr()
app = create_app()
