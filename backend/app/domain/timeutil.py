"""Время внутри рабочего дня: минуты от полуночи в коде, HH:MM в JSON."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer

DAY_MIN = 24 * 60

# Часы не ограничены сверху: в плане диспетчеров визит может уйти за полночь и дальше.
_HHMM = re.compile(r"^(\d{1,3}):(\d{2})$")


def parse_hhmm(value: str | int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"ожидается HH:MM, получено {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"минуты не могут быть отрицательными: {value}")
        return value
    match = _HHMM.match(str(value).strip())
    if not match:
        raise ValueError(f"ожидается HH:MM, получено {value!r}")
    hours, minutes = int(match.group(1)), int(match.group(2))
    if minutes > 59:
        raise ValueError(f"некорректное время: {value!r}")
    return hours * 60 + minutes


def fmt_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_beeline_datetime(value: str) -> int:
    """'17.08.2026 0:01' -> 1. Дата отбрасывается: данные за один день."""
    parts = value.strip().split()
    if len(parts) != 2:
        raise ValueError(f"ожидается 'ДД.ММ.ГГГГ Ч:ММ', получено {value!r}")
    return parse_hhmm(parts[1])


HHMM = Annotated[
    int,
    BeforeValidator(parse_hhmm),
    PlainSerializer(fmt_hhmm, return_type=str, when_used="json"),
]
