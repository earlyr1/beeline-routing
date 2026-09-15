"""Таймлайн датасета в API: текущее время плана, события на шкале и фоновый предподсчёт планов.

У датасета две блокировки. record.lock короткая: чтение состояния и замена record.session, record.cursor и событий
таймлайна. record.timeline_lock ставит в очередь изменения таймлайна, переносы времени с пересчётом и все решения
солвера. Солвер работает без record.lock, поэтому состояние, объяснения, линии маршрутов и чат отвечают во время
пересчёта. Порядок захвата: сначала timeline_lock, потом lock.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.api.registry import DatasetRecord
from app.api.schemas import PlanningState, TimelineItem, to_planning_state
from app.planning.session import PlanningContext
from app.planning.timeline import TimelineEntry, TimelineStep, Walk, replay_step

RunBackground = Callable[[Callable[[], None]], None]

# Пауза фонового предподсчёта между шагами: запрос, который ждёт timeline_lock, успевает его взять.
PRECOMPUTE_PAUSE_S = 0.05


def planning_state(record: DatasetRecord) -> PlanningState:
    """Состояние на текущее время плана с событиями таймлайна. Вызывать, когда план дня уже есть."""
    with record.lock:
        views, ready = record.timeline.view(record.base, record.cursor)
        timeline = [
            TimelineItem(id=view.entry.id, event=view.event, status=view.status, reason=view.reason)
            for view in views
        ]
        return to_planning_state(
            record.session, cursor=record.cursor, timeline=timeline, timeline_ready=ready
        )


def _replay_next(record: DatasetRecord, ctx: PlanningContext, walk: Walk, entry: TimelineEntry) -> None:
    """Под record.timeline_lock: считает шаг события entry после прохода walk без record.lock и сохраняет его."""
    version = record.next_version()
    step = replay_step(walk.session, entry, ctx, version)
    with record.lock:
        record.timeline.store(walk, entry, step)
        if step.applied is not None:
            record.use_version(version)


def compute_steps(record: DatasetRecord, ctx: PlanningContext, count: int) -> Walk:
    """Под record.timeline_lock: досчитывает шаги первых count событий по порядку и возвращает проход по ним."""
    while True:
        with record.lock:
            walk = record.timeline.walk(record.base, count)
            if walk.done >= min(count, len(record.timeline.entries)):
                return walk
            entry = record.timeline.entries[walk.done]
        _replay_next(record, ctx, walk, entry)


def move_cached(record: DatasetRecord, cursor: int) -> bool:
    """Под record.lock: переносит текущее время, если шаги до него посчитаны, и ставит план на это время."""
    count = record.timeline.applied_count(cursor)
    walk = record.timeline.walk(record.base, count)
    if walk.done < count:
        return False
    record.cursor = cursor
    record.session = walk.session
    return True


def settle(record: DatasetRecord, ctx: PlanningContext, cursor: int | None = None) -> None:
    """Под record.timeline_lock: ставит план на текущее время (или переносит время на cursor), досчитывая шаги."""
    while True:
        with record.lock:
            target = record.cursor if cursor is None else cursor
            if move_cached(record, target):
                return
            count = record.timeline.applied_count(target)
        compute_steps(record, ctx, count)


def insert_and_replay(record: DatasetRecord, ctx: PlanningContext, entry: TimelineEntry) -> TimelineStep:
    """Под record.timeline_lock: ставит событие на шкалу и считает его шаг после предыдущих событий.

    Отклонённое событие убирается со шкалы, а шаги событий перед ним остаются в кэше. Текущее время не меняется.
    """
    with record.lock:
        position = record.timeline.insert(entry)
    step = compute_steps(record, ctx, position + 1).steps[position]
    if step.reason is not None:
        with record.lock:
            record.timeline.remove(entry.id)
    return step


def move_cursor(record: DatasetRecord, ctx: PlanningContext, cursor: int) -> None:
    """Переносит текущее время. Если шаги до него посчитаны, солвер не нужен и очередь timeline_lock не ждём."""
    with record.lock:
        if move_cached(record, cursor):
            return
    with record.timeline_lock:
        settle(record, ctx, cursor)


def ensure_precompute(record: DatasetRecord, ctx: PlanningContext, run_background: RunBackground) -> None:
    """Запускает фоновый предподсчёт текущей ревизии таймлайна, если посчитаны не все шаги и он ещё не запущен.

    Если все шаги посчитаны, из кэша убираются шаги, которые остались от прежних версий таймлайна.
    """
    with record.lock:
        if record.base is None:
            return
        walk = record.timeline.walk(record.base)
        if walk.done == len(record.timeline.entries):
            record.timeline.prune(walk)
            return
        revision = record.timeline.revision
        if record.precompute_revision == revision:
            return
        record.precompute_revision = revision
    run_background(lambda: precompute(record, ctx, revision))


def precompute(
    record: DatasetRecord, ctx: PlanningContext, revision: int, pause_s: float = PRECOMPUTE_PAUSE_S
) -> None:
    """Считает шаги таймлайна по порядку, по одному шагу за захват record.timeline_lock.

    Останавливается, когда посчитаны все шаги или таймлайн сменил ревизию: предподсчёт новой ревизии запускает
    само изменение. Между шагами timeline_lock свободен для запросов.
    """
    while True:
        with record.timeline_lock:
            with record.lock:
                if record.timeline.revision != revision:
                    return
                walk = record.timeline.walk(record.base)
                if walk.done == len(record.timeline.entries):
                    record.timeline.prune(walk)
                    return
                entry = record.timeline.entries[walk.done]
            _replay_next(record, ctx, walk, entry)
        time.sleep(pause_s)
