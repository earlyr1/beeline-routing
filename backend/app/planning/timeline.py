"""Таймлайн дня: события, привязанные ко времени, и планы после каждого из них.

План на время T — это план начала дня и все события таймлайна со временем не позже T, применённые по порядку:
время события, затем порядок добавления. Шаг — применение одного события к плану после предыдущих. Шаги хранятся
в кэше по ключу «принятые события до шага, событие шага»: отклонённое событие план не меняет и в ключ не входит,
поэтому его добавление или удаление не сбрасывает следующие шаги, а принятое событие сбрасывает все шаги после себя.

Стратегия «ломающего» события (app/planning/variants.py) входит в ключ шага: у события с выбором номер в ключе вида
tl_3@keep. Событие без выбора останавливает проход, пока диспетчер не выберет.

Модуль без блокировок и потоков: порядок вычислений и хранение в датасете задаёт app/api/timeline.py.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from app.domain.enums import EventType, request_label
from app.domain.models import Event, Request
from app.domain.timeutil import DAY_MIN
from app.ingest.geocode import GeoResult
from app.planning.models import AppliedEvent, EventVariant
from app.planning.session import (
    EventRejected,
    PlanningContext,
    PlanningSession,
    apply_event,
    offline_context,
    replay_checked_event,
)
from app.planning.variants import VARIANTS, is_choosable

# Последняя минута дня: событие таймлайна и текущее время плана не позже неё.
LAST_MINUTE = DAY_MIN - 1
EVENT_TIME_RANGE_TEXT = "Время события должно быть от 00:00 до 23:59."
CURSOR_RANGE_TEXT = "время должно быть от 00:00 до 23:59"

TimelineStatus = Literal["applied", "pending", "rejected", "awaiting"]

# Переназначение заявки называет и заявку, и бригаду: проверяются оба номера.
_ENGINEER_EVENTS = frozenset(
    {
        EventType.ENGINEER_UNAVAILABLE,
        EventType.ENGINEER_TRANSPORT_CHANGED,
        EventType.ENGINEER_DELAYED,
        EventType.REQUEST_REASSIGNED,
    }
)
_REQUEST_EVENTS = frozenset(
    {EventType.CANCEL, EventType.RESTORE, EventType.REQUEST_UPDATED, EventType.REQUEST_REASSIGNED}
)


@dataclass(frozen=True)
class TimelineEntry:
    """Событие на шкале.

    event — событие как его прислали, без полей, которые заполняет backend (previous_*); у срочной заявки уже есть
    координаты. geo — ответы геокодера на новые адреса, полученные при добавлении. checked — событие из подтверждённого
    предложения помощника: его координаты дал геокодер при проверке (replay_checked_event). variant — выбранная
    стратегия «ломающего» события, None — не выбрана или событие не «ломающее».
    """

    id: str
    seq: int
    event: Event
    geo: Mapping[str, GeoResult] = field(default_factory=dict)
    checked: bool = False
    variant: EventVariant | None = None

    @property
    def order(self) -> tuple[int, int]:
        return self.event.time, self.seq

    def replay_input(self, ctx: PlanningContext) -> tuple[Event, PlanningContext]:
        """Событие и контекст для apply_event без обращений к геокодеру."""
        if self.checked:
            return replay_checked_event(self.event, offline_context(ctx, {}))
        return self.event, offline_context(ctx, self.geo)


@dataclass(frozen=True)
class TimelineStep:
    """Результат применения события к плану после предыдущих: новая сессия или прежняя и причина отказа."""

    session: PlanningSession
    applied: AppliedEvent | None = None
    reason: str | None = None


StepKey = tuple[tuple[str, ...], str]


def entry_token(entry: TimelineEntry, variant: EventVariant | None = None) -> str:
    """Событие в ключе кэша шагов: у события со стратегией к номеру добавляется стратегия («tl_3@keep»)."""
    chosen = variant or entry.variant
    return f"{entry.id}@{chosen}" if chosen is not None else entry.id


def _token_entry(token: str) -> str:
    return token.split("@", 1)[0]


@dataclass(frozen=True)
class Walk:
    """Проход по событиям через кэш шагов от начала дня до первого непосчитанного шага.

    session — план после посчитанных шагов, prefix — принятые события в нём, steps и keys — посчитанные шаги по
    порядку событий. awaiting — «ломающее» событие без выбора, на котором проход остановился; его варианты посчитаны.
    """

    session: PlanningSession
    prefix: tuple[str, ...]
    steps: list[TimelineStep]
    keys: list[StepKey]
    awaiting: TimelineEntry | None = None

    @property
    def done(self) -> int:
        return len(self.steps)


@dataclass(frozen=True)
class TimelineView:
    """Событие на шкале для ответа API: применённое событие, если оно применено, иначе запланированное."""

    entry: TimelineEntry
    event: Event
    status: TimelineStatus
    reason: str | None = None


def replay_step(
    prior: PlanningSession,
    entry: TimelineEntry,
    ctx: PlanningContext,
    version: int,
    variant: EventVariant | None = None,
) -> TimelineStep:
    """Применяет событие к плану prior со стратегией variant (по умолчанию выбранной в событии, иначе optimal).

    Отклонённое событие оставляет план прежним и сохраняет причину.
    """
    event, replay_ctx = entry.replay_input(ctx)
    try:
        session = apply_event(
            prior, event, replay_ctx, version=version, variant=variant or entry.variant or "optimal"
        )
    except EventRejected as error:
        return TimelineStep(session=prior, reason=str(error))
    return TimelineStep(session=session, applied=session.events[-1])


def _urgent_requests(entries: Sequence[TimelineEntry]) -> list[tuple[TimelineEntry, Request]]:
    return [
        (entry, entry.event.request)
        for entry in entries
        if entry.event.type == EventType.URGENT and entry.event.request is not None
    ]


def known_requests(base: PlanningSession, entries: Sequence[TimelineEntry]) -> dict[str, Request]:
    """Заявки дня и срочные заявки из событий таймлайна по номеру."""
    known = {request.id: request for request in base.requests}
    for _, request in _urgent_requests(entries):
        known.setdefault(request.id, request)
    return known


def check_known(base: PlanningSession, entries: Sequence[TimelineEntry], event: Event) -> None:
    """Проверяет номера нового события таймлайна до пересчёта. Бросает EventRejected с текстом для диспетчера.

    Инженеры берутся из дня: событий, которые добавляют инженеров, нет. Заявка известна, если она есть в дне или её
    добавляет срочная заявка, которая стоит на шкале раньше нового события. Номер срочной заявки не должен
    повторять ни заявку дня, ни срочную заявку из любого события таймлайна.
    """
    urgent = _urgent_requests(entries)
    if event.type in _ENGINEER_EVENTS and base.engineer(event.engineer_id or "") is None:
        raise EventRejected(f"Инженер {event.engineer_id} не найден.")
    if event.type == EventType.URGENT and event.request is not None:
        new_id = event.request.id
        taken = base.request(new_id) or next((request for _, request in urgent if request.id == new_id), None)
        if taken is not None:
            raise EventRejected(
                f"Заявка с номером {request_label(taken.id, taken.priority)} уже есть в плане."
            )
    if event.type in _REQUEST_EVENTS:
        earlier = {request.id for entry, request in urgent if entry.event.time <= event.time}
        if base.request(event.request_id or "") is None and event.request_id not in earlier:
            raise EventRejected(f"Заявка {event.request_id} не найдена.")


@dataclass
class Timeline:
    """События таймлайна одного дня по порядку применения и кэш шагов."""

    entries: list[TimelineEntry] = field(default_factory=list)
    steps: dict[StepKey, TimelineStep] = field(default_factory=dict)
    # Меняется при каждом изменении списка событий: фоновый предподсчёт старой ревизии останавливается.
    revision: int = 0
    # Номер последнего созданного события: номера tl_<n> не повторяются, в том числе после пересборки дня.
    last_number: int = 0

    def create(
        self,
        event: Event,
        geo: Mapping[str, GeoResult] | None = None,
        *,
        checked: bool = False,
        variant: EventVariant | None = None,
    ) -> TimelineEntry:
        """Новое событие со следующим номером; в таймлайн его добавляет insert. variant — стратегия сразу."""
        self.last_number += 1
        sent = event.model_copy(
            update={"previous_transport": None, "previous_request": None, "previous_engineer_id": None}
        )
        return TimelineEntry(
            id=f"tl_{self.last_number}",
            seq=self.last_number,
            event=sent,
            geo=dict(geo or {}),
            checked=checked,
            variant=variant,
        )

    def insert(self, entry: TimelineEntry) -> int:
        """Ставит событие на его место по времени и порядку добавления и возвращает позицию."""
        position = bisect.bisect_right([item.order for item in self.entries], entry.order)
        self.entries.insert(position, entry)
        self.revision += 1
        return position

    def find(self, entry_id: str) -> TimelineEntry | None:
        return next((entry for entry in self.entries if entry.id == entry_id), None)

    def remove(self, entry_id: str) -> TimelineEntry | None:
        """Убирает событие и шаги, в которых оно участвовало. Возвращает событие или None, если его нет."""
        entry = self.find(entry_id)
        if entry is None:
            return None
        self.entries.remove(entry)
        self.steps = {
            key: step
            for key, step in self.steps.items()
            if _token_entry(key[1]) != entry_id and entry_id not in {_token_entry(token) for token in key[0]}
        }
        self.revision += 1
        return entry

    def set_variant(self, entry_id: str, variant: EventVariant) -> TimelineEntry | None:
        """Выбор или смена стратегии события. Шаги с другой стратегией остаются в кэше под своими ключами."""
        entry = self.find(entry_id)
        if entry is None:
            return None
        chosen = replace(entry, variant=variant)
        self.entries[self.entries.index(entry)] = chosen
        self.revision += 1
        return chosen

    def clear(self) -> None:
        self.entries = []
        self.steps = {}
        self.revision += 1

    def applied_count(self, cursor: int) -> int:
        """Сколько первых событий применено к плану на время cursor (события ровно в cursor применены)."""
        return bisect.bisect_right([entry.event.time for entry in self.entries], cursor)

    def walk(self, base: PlanningSession, count: int | None = None) -> Walk:
        """Проходит первые count событий (все, если count не задан) по кэшу, пока шаги посчитаны.

        «Ломающее» событие без выбора останавливает проход: если его шаги посчитаны для всех стратегий, оно
        становится awaiting. Событие, отклонённое при optimal, выбора не требует и проходится как отклонённое.
        """
        limit = len(self.entries) if count is None else min(count, len(self.entries))
        session, prefix = base, ()
        steps: list[TimelineStep] = []
        keys: list[StepKey] = []
        awaiting: TimelineEntry | None = None
        for entry in self.entries[:limit]:
            if is_choosable(entry.event) and entry.variant is None:
                probe_key = (prefix, entry_token(entry, "optimal"))
                probe = self.steps.get(probe_key)
                if probe is not None and probe.reason is not None:
                    steps.append(probe)
                    keys.append(probe_key)
                    continue
                if probe is not None and all(
                    (prefix, entry_token(entry, variant)) in self.steps for variant in VARIANTS
                ):
                    awaiting = entry
                break
            key = (prefix, entry_token(entry))
            step = self.steps.get(key)
            if step is None:
                break
            steps.append(step)
            keys.append(key)
            session = step.session
            if step.applied is not None:
                prefix = (*prefix, key[1])
        return Walk(session=session, prefix=prefix, steps=steps, keys=keys, awaiting=awaiting)

    def step(self, walk: Walk, entry: TimelineEntry, variant: EventVariant) -> TimelineStep | None:
        """Шаг события entry со стратегией variant после прохода walk, если он посчитан."""
        return self.steps.get((walk.prefix, entry_token(entry, variant)))

    def store(
        self, walk: Walk, entry: TimelineEntry, step: TimelineStep, variant: EventVariant | None = None
    ) -> None:
        """Сохраняет шаг события entry, следующего за проходом walk, со стратегией variant или выбранной."""
        self.steps[(walk.prefix, entry_token(entry, variant))] = step

    def prune(self, walk: Walk) -> None:
        """Оставляет в кэше шаги полного прохода и другие стратегии его событий: выбор можно поменять без пересчёта."""
        if walk.done != len(self.entries):
            return
        keep = set(walk.keys)
        for prefix, token in walk.keys:
            if "@" in token:
                keep.update((prefix, f"{_token_entry(token)}@{variant}") for variant in VARIANTS)
        self.steps = {key: step for key, step in self.steps.items() if key in keep}

    def pending_choice(self, base: PlanningSession, cursor: int) -> tuple[Walk, TimelineEntry] | None:
        """Событие, на котором стоит текущее время и которое ждёт выбора, и проход до него."""
        walk = self.walk(base, self.applied_count(cursor))
        if walk.awaiting is None or walk.awaiting.event.time > cursor:
            return None
        return walk, walk.awaiting

    def view(self, base: PlanningSession, cursor: int) -> tuple[list[TimelineView], bool]:
        """События для ответа API и признак, что считать больше нечего.

        Отклонённое событие видно сразу, как только посчитан его шаг, даже если оно позже текущего времени.
        Принятое событие не позже cursor применено; «ломающее» событие без выбора, до которого дошло время,
        ждёт выбора; остальные ждут своего времени или пересчёта. Остановка на выборе считается готовностью:
        дальше считать нельзя, пока диспетчер не выберет.
        """
        walk = self.walk(base)
        items: list[TimelineView] = []
        for k, entry in enumerate(self.entries):
            step = walk.steps[k] if k < walk.done else None
            if step is not None and step.reason is not None:
                items.append(TimelineView(entry, entry.event, "rejected", step.reason))
            elif step is not None and step.applied is not None and entry.event.time <= cursor:
                items.append(TimelineView(entry, step.applied.event, "applied"))
            elif entry is walk.awaiting and entry.event.time <= cursor:
                items.append(TimelineView(entry, entry.event, "awaiting"))
            else:
                items.append(TimelineView(entry, entry.event, "pending"))
        return items, walk.done == len(self.entries) or walk.awaiting is not None
