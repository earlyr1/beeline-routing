"""Сетка окон визита: рабочий день, нарезанный на слоты одной длины.

Диспетчер не называет клиенту произвольный интервал вроде 11:30–13:30 — он предлагает слот. Так устроены
настоящие данные: у 205 заявок трёх реальных регионов выгрузки окон ровно шесть — 10:00–12:00 (42),
12:00–14:00 (43), 14:00–16:00 (28), 16:00–18:00 (28), 18:00–20:00 (31) и 20:00–22:00 (22), а у 11 аварий
стоит весь день, 00:01–23:59. Смена при этом 10:00–22:00, то есть сетка — ровно рабочий день по два часа.

Сетку строит конфиг (SynthConfig.window_grid): день она берёт из смен, длину слота — из длины окна, поэтому
разойтись со сменой не может. Проверяют сеткой только то, что ВЫБРАЛ диспетчер (app/api/routes.py). Заявки
ДАННЫХ — выгрузка Билайна, бандлы, контрольные файлы — сеткой не проверяются: настоящие данные и есть источник
правды, а окно аварии на весь день слотом не является и должно работать как есть.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from app.domain.timeutil import HHMM, fmt_hhmm


class TimeSlot(BaseModel):
    """Слот сетки: окно, которое диспетчер называет клиенту."""

    start: HHMM
    end: HHMM


def build_slots(day_start: int, day_end: int, slot_min: int) -> list[TimeSlot]:
    """Слоты длиной slot_min подряд от начала рабочего дня.

    Хвост короче слота в сетку не попадает: клиенту называют окно полной длины, а не огрызок. Для смены
    10:00–22:00 и окна 120 минут это ровно шесть слотов реальных данных.
    """
    if slot_min <= 0:
        raise ValueError("длина окна визита должна быть больше нуля")
    return [
        TimeSlot(start=start, end=start + slot_min)
        for start in range(day_start, day_end - slot_min + 1, slot_min)
    ]


def slot_for(slots: Sequence[TimeSlot], minute: int) -> TimeSlot | None:
    """Слот, в который попадает минута, иначе ближайший к ней; None — сетки нет.

    Граница достаётся следующему слоту: 12:00 — это начало 12:00–14:00, а не конец 10:00–12:00. Минута вне
    рабочего дня (визит уехал за смену) слота не имеет — тогда называют ближайший, других вариантов у сетки нет.
    """
    if not slots:
        return None
    inside = next((slot for slot in slots if slot.start <= minute < slot.end), None)
    if inside is not None:
        return inside
    return min(slots, key=lambda slot: min(abs(slot.start - minute), abs(slot.end - minute)))


def is_slot(slots: Sequence[TimeSlot], start: int, end: int) -> bool:
    """Окно совпадает со слотом сетки."""
    return any(slot.start == start and slot.end == end for slot in slots)


def slots_text(slots: Sequence[TimeSlot]) -> str:
    """Сетка для диспетчера: «10:00–12:00, 12:00–14:00, …»."""
    return ", ".join(f"{fmt_hhmm(slot.start)}–{fmt_hhmm(slot.end)}" for slot in slots)


def off_grid_text(label: str, start: int, end: int, slots: Sequence[TimeSlot]) -> str:
    """Отказ диспетчеру: такого окна клиенту не называют."""
    return (
        f"Окно заявки {label} {fmt_hhmm(start)}–{fmt_hhmm(end)} не из сетки окон: клиенту называют слот. "
        f"Выберите один из: {slots_text(slots)}."
    )
