"""Таймлайн датасета в API: текущее время плана, события на шкале и фоновый предподсчёт планов.

У датасета две блокировки. record.lock короткая: чтение состояния и замена record.session, record.cursor и событий
таймлайна. record.timeline_lock ставит в очередь изменения таймлайна, переносы времени с пересчётом и все решения
солвера. Солвер работает без record.lock, поэтому состояние, объяснения, линии маршрутов и чат отвечают во время
пересчёта. Порядок захвата: сначала timeline_lock, потом lock.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app.api.registry import DatasetRecord
from app.api.schemas import PlanningState, TimelineItem, to_planning_state
from app.domain.models import Event
from app.domain.timeutil import fmt_hhmm
from app.planning.models import BaseVariant, EventChoice, EventVariant
from app.planning.session import PlanningContext
from app.planning.timeline import TimelineEntry, TimelineStep, Walk, replay_step
from app.planning.variants import (
    VARIANTS,
    Outcome,
    assign_variant,
    assigned_engineer,
    build_choice,
    is_assignable,
    is_choosable,
    takes_variant,
)
from app.state.repo import StateConflict, StateUnavailable

logger = logging.getLogger(__name__)

RunBackground = Callable[[Callable[[], None]], None]

# Пауза фонового предподсчёта между шагами: запрос, который ждёт timeline_lock, успевает его взять.
PRECOMPUTE_PAUSE_S = 0.05
NOT_CHOOSABLE_TEXT = "Для этого события варианты не предлагаются."
NOT_ASSIGNABLE_TEXT = "Бригаду можно выбрать только для срочной заявки."


class VariantUnavailable(Exception):
    """Варианты события получить нельзя: status — код ответа API, текст — для диспетчера."""

    def __init__(self, status: int, text: str) -> None:
        super().__init__(text)
        self.status = status


def check_variant(record: DatasetRecord, event: Event, variant: EventVariant) -> None:
    """Под record.lock: проверяет стратегию из запроса для события. Бросает VariantUnavailable.

    По порядку: незнакомая строка — 422 у любого события, событие без стратегий — 409, «отдать бригаде» не
    на срочной заявке или с бригадой не из этого дня — 422. Базовые стратегии подходят любому событию со
    стратегией: «ломающему» и отмене заявки, у которой окна выбора нет, а стратегия есть.
    """
    engineer_id = assigned_engineer(variant)
    if variant not in VARIANTS and engineer_id is None:
        raise VariantUnavailable(422, f"Неизвестный вариант «{variant}».")
    if not takes_variant(event):
        raise VariantUnavailable(409, NOT_CHOOSABLE_TEXT)
    if engineer_id is None:
        return
    if not is_assignable(event):
        raise VariantUnavailable(422, NOT_ASSIGNABLE_TEXT)
    if record.base is None or record.base.engineer(engineer_id) is None:
        raise VariantUnavailable(422, f"Инженер {engineer_id} не найден.")


def _choice(
    record: DatasetRecord, walk: Walk, entry: TimelineEntry, assign: EventVariant | None = None
) -> EventChoice:
    """Под record.lock: варианты события из посчитанных шагов его стратегий.

    assign — стратегия «отдать бригаде», если диспетчер её назвал: она идёт в окне четвёртой.
    """
    outcomes = []
    for variant in (*VARIANTS, *([assign] if assign is not None else [])):
        step = record.timeline.step(walk, entry, variant)
        if step is not None and step.applied is not None and step.session.last_diff is not None:
            outcomes.append(Outcome(variant, step.session.plan, step.session.last_diff))
    names = {engineer.id: engineer.name for engineer in walk.session.engineers}
    return build_choice(entry.id, entry.event, walk.session.plan, outcomes, entry.variant, names)


def planning_state(record: DatasetRecord) -> PlanningState:
    """Состояние на текущее время плана с событиями таймлайна. Вызывать, когда план дня уже есть."""
    with record.lock:
        views, ready = record.timeline.view(record.day_base(), record.cursor)
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
        pending = record.timeline.pending_choice(record.day_base(), record.cursor)
        assert record.session is not None  # план на текущее время есть вместе с планом начала дня
        return to_planning_state(
            record.session,
            cursor=record.cursor,
            timeline=timeline,
            timeline_ready=ready,
            pending_choice=_choice(record, *pending) if pending is not None else None,
            # Утро дня — состояние до событий шкалы, а не первая загрузка файла: пересборка дня делает его заново.
            morning=record.base,
            # Регион дня сгенерирован нами: вкладка «Сравнение» так и подписывает колонку «Диспетчеры».
            generated=record.prepared is not None and record.prepared.generated,
            # Что клиентам уже сказали по телефону: отметки вкладки «Коммуникации» живут на сервере.
            agreed=record.agreed,
        )


def _replay_next(record: DatasetRecord, ctx: PlanningContext, walk: Walk, entry: TimelineEntry) -> None:
    """Под record.timeline_lock: считает шаг события entry после прохода walk без record.lock и сохраняет его."""
    version = record.next_version()
    step = replay_step(walk.session, entry, ctx, version)
    record.store_step(walk, entry, step, version=version)


def _replay_variants(record: DatasetRecord, ctx: PlanningContext, walk: Walk, entry: TimelineEntry) -> None:
    """Под record.timeline_lock: шаги события entry для всех стратегий после прохода walk, без record.lock.

    Посчитанные стратегии не пересчитываются. Отклонение не зависит от стратегии: после отклонённого optimal
    остальные не сохраняются. Все стратегии получают один номер плана: в цепочку попадёт только выбранная.
    С пулом процессов недостающие стратегии считаются одновременно, без него — по очереди до первого отклонения.
    """
    version = record.next_version()
    with record.lock:
        known = {variant: record.timeline.step(walk, entry, variant) for variant in VARIANTS}
    missing = [variant for variant in VARIANTS if known[variant] is None]
    rejected = known["optimal"] is not None and known["optimal"].reason is not None
    fresh: dict[BaseVariant, TimelineStep] = {}
    if ctx.solver_pool is not None and len(missing) > 1 and not rejected:
        with ThreadPoolExecutor(max_workers=len(missing)) as threads:
            futures = {
                variant: threads.submit(replay_step, walk.session, entry, ctx, version, variant)
                for variant in missing
            }
            fresh = {variant: future.result() for variant, future in futures.items()}
        if "optimal" in fresh and fresh["optimal"].reason is not None:
            fresh = {"optimal": fresh["optimal"]}
    else:
        for variant in VARIANTS:
            step = known[variant]
            if step is None:
                step = fresh[variant] = replay_step(walk.session, entry, ctx, version, variant)
            if step.reason is not None:
                break
    with record.lock:
        for variant, step in fresh.items():
            record.store_step(walk, entry, step, variant, version=version)


def _replay_assign(
    record: DatasetRecord, ctx: PlanningContext, walk: Walk, entry: TimelineEntry, variant: EventVariant
) -> None:
    """Под record.timeline_lock: шаг события entry со стратегией «отдать бригаде», если он ещё не посчитан.

    Такие шаги не предподсчитываются: диспетчер сначала называет бригаду, и только тогда считается один план.
    Посчитанный шаг лежит в кэше под своим ключом, поэтому повторное открытие окна решатель не запускает.
    """
    with record.lock:
        if record.timeline.step(walk, entry, variant) is not None:
            return
    version = record.next_version()
    step = replay_step(walk.session, entry, ctx, version, variant)
    record.store_step(walk, entry, step, variant, version=version)


def compute_steps(record: DatasetRecord, ctx: PlanningContext, count: int) -> Walk:
    """Под record.timeline_lock: досчитывает шаги первых count событий по порядку и возвращает проход по ним.

    На «ломающем» событии без выбора считаются все его стратегии, и проход останавливается на нём.
    """
    while True:
        with record.lock:
            walk = record.timeline.walk(record.day_base(), count)
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
    walk = record.timeline.walk(record.day_base(), count)
    if walk.awaiting is not None:
        record.move_to(walk.awaiting.event.time, walk.session)
        return True
    if walk.done < count:
        return False
    record.move_to(cursor, walk.session)
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
    position = record.add_entry(entry)
    walk = compute_steps(record, ctx, position + 1)
    step = walk.steps[position] if walk.done > position else None
    if step is not None and step.reason is not None:
        record.drop_entry(entry.id)
    return step


def event_choice(
    record: DatasetRecord, ctx: PlanningContext, entry_id: str, assign: str | None = None
) -> EventChoice:
    """Варианты события шкалы для окна выбора: считает недостающие стратегии. Бросает VariantUnavailable.

    assign — номер бригады: к трём вариантам добавляется четвёртый, «отдать заявку этой бригаде». Его план
    считается здесь же, по одному запросу, и остаётся в кэше шагов.
    """
    with record.timeline_lock:
        with record.lock:
            entry = record.timeline.find(entry_id)
            if entry is None:
                raise VariantUnavailable(404, f"Событие {entry_id} не найдено.")
            if not is_choosable(entry.event):
                raise VariantUnavailable(409, NOT_CHOOSABLE_TEXT)
            variant = assign_variant(assign) if assign is not None else None
            if variant is not None:
                check_variant(record, entry.event, variant)
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
        if variant is not None:
            _replay_assign(record, ctx, walk, entry, variant)
        with record.lock:
            return _choice(record, walk, entry, variant)


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
            record.prune_steps(walk)
            return
        if walk.awaiting is not None:
            return
        revision = record.timeline.revision
        if record.precompute_revision == revision:
            return
        record.precompute_revision = revision
        # Пока фоновый счёт идёт, запись не забывается из памяти реестра: иначе следующее обращение подняло бы
        # из базы второй экземпляр того же дня. Счётчик, а не флаг: предподсчёт новой ревизии может начаться,
        # пока прежний ещё не дошёл до проверки ревизии.
        record.precomputing += 1
    try:
        run_background(lambda: precompute(record, ctx, revision))
    except Exception:
        # Фоновая задача не запустилась: держать запись в памяти незачем, а ревизию нужно отпустить, иначе
        # предподсчёт этой ревизии больше никто не начнёт.
        with record.lock:
            record.precomputing -= 1
            record.precompute_revision = None
        raise


def precompute(
    record: DatasetRecord, ctx: PlanningContext, revision: int, pause_s: float = PRECOMPUTE_PAUSE_S
) -> None:
    """Считает шаги таймлайна по порядку, по одному шагу за захват record.timeline_lock.

    Останавливается, когда посчитаны все шаги или таймлайн сменил ревизию: предподсчёт новой ревизии запускает
    само изменение. Между шагами timeline_lock свободен для запросов.

    Досчитав шаги, доводит план до текущего времени: обычно он там и стоит, но у дня, только что поднятого
    из базы, шага могло не хватать — его и ждало текущее время.
    """
    try:
        while True:
            with record.timeline_lock:
                with record.lock:
                    if record.timeline.revision != revision:
                        return
                    walk = record.timeline.walk(record.day_base())
                    ready = walk.done == len(record.timeline.entries)
                    if ready:
                        record.prune_steps(walk)
                    # Ждёт выбора или считать нечего: следующего шага нет.
                    entry = None if ready or walk.awaiting is not None else record.timeline.entries[walk.done]
                if entry is None:
                    # Часы, отведённые назад при подъёме дня из базы, возвращаются на своё время: шаг, которого
                    # тогда не хватало, теперь посчитан.
                    settle(record, ctx, record.take_resume())
                    return
                if is_choosable(entry.event) and entry.variant is None:
                    _replay_variants(record, ctx, walk, entry)
                else:
                    _replay_next(record, ctx, walk, entry)
            time.sleep(pause_s)
    except (StateUnavailable, StateConflict) as error:
        # База отвалилась посреди счёта: это не поломка сервиса, шаг просто не сохранился. Пишем в лог и
        # выходим — ревизию отпустит finally, и следующий /state начнёт предподсчёт заново.
        logger.warning("Фоновый предподсчёт дня %s прерван: %s", record.dataset_id, error)
    finally:
        with record.lock:
            record.precomputing -= 1
            # Ревизия отпускается в любом случае: упавший счёт должен запускаться заново, а досчитанный
            # ensure_precompute второй раз не начнёт — у полного прохода считать уже нечего.
            record.precompute_revision = None
