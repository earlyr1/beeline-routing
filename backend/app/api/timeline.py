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
from app.domain.timeutil import fmt_hhmm
from app.planning.models import EventChoice, EventVariant
from app.planning.session import PlanningContext
from app.planning.timeline import TimelineEntry, TimelineStep, Walk, replay_step
from app.planning.variants import VARIANTS, Outcome, build_choice, is_choosable

RunBackground = Callable[[Callable[[], None]], None]

# Пауза фонового предподсчёта между шагами: запрос, который ждёт timeline_lock, успевает его взять.
PRECOMPUTE_PAUSE_S = 0.05
NOT_CHOOSABLE_TEXT = "Для этого события варианты не предлагаются."


class VariantUnavailable(Exception):
    """Варианты события получить нельзя: status — код ответа API, текст — для диспетчера."""

    def __init__(self, status: int, text: str) -> None:
        super().__init__(text)
        self.status = status


def _choice(record: DatasetRecord, walk: Walk, entry: TimelineEntry) -> EventChoice:
    """Под record.lock: варианты события из посчитанных шагов его стратегий."""
    outcomes = []
    for variant in VARIANTS:
        step = record.timeline.step(walk, entry, variant)
        if step is not None and step.applied is not None and step.session.last_diff is not None:
            outcomes.append(Outcome(variant, step.session.plan, step.session.last_diff))
    return build_choice(entry.id, entry.event, walk.session.plan, outcomes, entry.variant)


def planning_state(record: DatasetRecord) -> PlanningState:
    """Состояние на текущее время плана с событиями таймлайна. Вызывать, когда план дня уже есть."""
    with record.lock:
        views, ready = record.timeline.view(record.base, record.cursor)
        timeline = [
            TimelineItem(
                id=view.entry.id,
                event=view.event,
                status=view.status,
                reason=view.reason,
                variant=view.entry.variant,
                choosable=is_choosable(view.entry.event),
            )
            for view in views
        ]
        pending = record.timeline.pending_choice(record.base, record.cursor)
        return to_planning_state(
            record.session,
            cursor=record.cursor,
            timeline=timeline,
            timeline_ready=ready,
            pending_choice=_choice(record, *pending) if pending is not None else None,
        )


def _replay_next(record: DatasetRecord, ctx: PlanningContext, walk: Walk, entry: TimelineEntry) -> None:
    """Под record.timeline_lock: считает шаг события entry после прохода walk без record.lock и сохраняет его."""
    version = record.next_version()
    step = replay_step(walk.session, entry, ctx, version)
    with record.lock:
        record.timeline.store(walk, entry, step)
        if step.applied is not None:
            record.use_version(version)


def _replay_variants(record: DatasetRecord, ctx: PlanningContext, walk: Walk, entry: TimelineEntry) -> None:
    """Под record.timeline_lock: шаги события entry для всех стратегий после прохода walk, без record.lock.

    Посчитанные стратегии не пересчитываются. Отклонение не зависит от стратегии: после отклонённого optimal
    остальные не считаются. Все стратегии получают один номер плана: в цепочку попадёт только выбранная.
    """
    version = record.next_version()
    fresh: dict[EventVariant, TimelineStep] = {}
    for variant in VARIANTS:
        with record.lock:
            step = record.timeline.step(walk, entry, variant)
        if step is None:
            step = fresh[variant] = replay_step(walk.session, entry, ctx, version, variant)
        if step.reason is not None:
            break
    with record.lock:
        for variant, step in fresh.items():
            record.timeline.store(walk, entry, step, variant)
        if any(step.applied is not None for step in fresh.values()):
            record.use_version(version)


def compute_steps(record: DatasetRecord, ctx: PlanningContext, count: int) -> Walk:
    """Под record.timeline_lock: досчитывает шаги первых count событий по порядку и возвращает проход по ним.

    На «ломающем» событии без выбора считаются все его стратегии, и проход останавливается на нём.
    """
    while True:
        with record.lock:
            walk = record.timeline.walk(record.base, count)
            if walk.awaiting is not None or walk.done >= min(count, len(record.timeline.entries)):
                return walk
            entry = record.timeline.entries[walk.done]
        if is_choosable(entry.event) and entry.variant is None:
            _replay_variants(record, ctx, walk, entry)
        else:
            _replay_next(record, ctx, walk, entry)


def move_cached(record: DatasetRecord, cursor: int) -> bool:
    """Под record.lock: переносит текущее время, если шаги до него посчитаны, и ставит план на это время.

    Время не проходит «ломающее» событие без выбора: оно встаёт на время события, план — план до него.
    """
    count = record.timeline.applied_count(cursor)
    walk = record.timeline.walk(record.base, count)
    if walk.awaiting is not None:
        record.cursor = walk.awaiting.event.time
        record.session = walk.session
        return True
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


def insert_and_replay(
    record: DatasetRecord, ctx: PlanningContext, entry: TimelineEntry
) -> TimelineStep | None:
    """Под record.timeline_lock: ставит событие на шкалу и считает его шаг после предыдущих событий.

    Отклонённое событие убирается со шкалы, а шаги событий перед ним остаются в кэше. Текущее время не меняется.
    None — шага нет: событие ждёт выбора варианта или выбора ждёт событие раньше него.
    """
    with record.lock:
        position = record.timeline.insert(entry)
    walk = compute_steps(record, ctx, position + 1)
    step = walk.steps[position] if walk.done > position else None
    if step is not None and step.reason is not None:
        with record.lock:
            record.timeline.remove(entry.id)
    return step


def event_choice(record: DatasetRecord, ctx: PlanningContext, entry_id: str) -> EventChoice:
    """Варианты события шкалы для окна выбора: считает недостающие стратегии. Бросает VariantUnavailable."""
    with record.timeline_lock:
        with record.lock:
            entry = record.timeline.find(entry_id)
            if entry is None:
                raise VariantUnavailable(404, f"Событие {entry_id} не найдено.")
            if not is_choosable(entry.event):
                raise VariantUnavailable(409, NOT_CHOOSABLE_TEXT)
            position = record.timeline.entries.index(entry)
        walk = compute_steps(record, ctx, position)
        if walk.done < position:
            earlier = walk.awaiting.event.time if walk.awaiting is not None else entry.event.time
            raise VariantUnavailable(409, f"Сначала выберите вариант для события в {fmt_hhmm(earlier)}.")
        _replay_variants(record, ctx, walk, entry)
        with record.lock:
            optimal = record.timeline.step(walk, entry, "optimal")
            if optimal is not None and optimal.reason is not None:
                raise VariantUnavailable(409, f"Событие отклонено: {optimal.reason}")
            return _choice(record, walk, entry)


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
        if walk.awaiting is not None:
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
                if walk.awaiting is not None:
                    return
                entry = record.timeline.entries[walk.done]
            if is_choosable(entry.event) and entry.variant is None:
                _replay_variants(record, ctx, walk, entry)
            else:
                _replay_next(record, ctx, walk, entry)
        time.sleep(pause_s)
