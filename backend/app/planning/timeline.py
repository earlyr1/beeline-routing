"""Таймлайн дня: события, привязанные ко времени, и планы после каждого из них.

План на время T — это план начала дня и все события таймлайна со временем не позже T, применённые по порядку:
время события, затем порядок добавления. Шаг — применение одного события к плану после предыдущих. Шаги хранятся
в кэше по ключу «принятые события до шага, событие шага»: отклонённое событие план не меняет и в ключ не входит,
поэтому его добавление или удаление не сбрасывает следующие шаги, а принятое событие сбрасывает все шаги после себя.

Модуль без блокировок и потоков: порядок вычислений и хранение в датасете задаёт app/api/timeline.py.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.domain.enums import EventType
from app.domain.models import Event, Request
from app.domain.timeutil import DAY_MIN
from app.ingest.geocode import GeoResult
from app.planning.models import AppliedEvent
from app.planning.session import (
    EventRejected,
    PlanningContext,
    PlanningSession,
    apply_event,
    offline_context,
    replay_checked_event,
)

# Последняя минута дня: событие таймлайна и текущее время плана не позже неё.
LAST_MINUTE = DAY_MIN - 1
EVENT_TIME_RANGE_TEXT = "Время события должно быть от 00:00 до 23:59."
CURSOR_RANGE_TEXT = "время должно быть от 00:00 до 23:59"

TimelineStatus = Literal["applied", "pending", "rejected"]

_ENGINEER_EVENTS = frozenset(
    {EventType.ENGINEER_UNAVAILABLE, EventType.ENGINEER_TRANSPORT_CHANGED, EventType.ENGINEER_DELAYED}
)
_REQUEST_EVENTS = frozenset({EventType.CANCEL, EventType.RESTORE, EventType.REQUEST_UPDATED})


@dataclass(frozen=True)
class TimelineEntry:
    """Событие на шкале.

    event — событие как его прислали, без полей, которые заполняет backend (previous_*); у срочной заявки уже есть
    координаты. geo — ответы геокодера на новые адреса, полученные при добавлении. checked — событие из подтверждённого
    предложения помощника: его координаты дал геокодер при проверке (replay_checked_event).
    """

    id: str
    seq: int
    event: Event
    geo: Mapping[str, GeoResult] = field(default_factory=dict)
    checked: bool = False

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


@dataclass(frozen=True)
class Walk:
    """Проход по событиям через кэш шагов от начала дня до первого непосчитанного шага.

    session — план после посчитанных шагов, prefix — принятые события в нём, steps и keys — посчитанные шаги по
    порядку событий.
    """

    session: PlanningSession
    prefix: tuple[str, ...]
    steps: list[TimelineStep]
    keys: list[StepKey]

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
    prior: PlanningSession, entry: TimelineEntry, ctx: PlanningContext, version: int
) -> TimelineStep:
    """Применяет событие к плану prior. Отклонённое событие оставляет план прежним и сохраняет причину."""
    event, replay_ctx = entry.replay_input(ctx)
    try:
        session = apply_event(prior, event, replay_ctx, version=version)
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
        if base.request(new_id) is not None or any(request.id == new_id for _, request in urgent):
            raise EventRejected(f"Заявка с номером {new_id} уже есть в плане.")
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
        self, event: Event, geo: Mapping[str, GeoResult] | None = None, *, checked: bool = False
    ) -> TimelineEntry:
        """Новое событие со следующим номером; в таймлайн его добавляет insert."""
        self.last_number += 1
        sent = event.model_copy(update={"previous_transport": None, "previous_request": None})
        return TimelineEntry(
            id=f"tl_{self.last_number}",
            seq=self.last_number,
            event=sent,
            geo=dict(geo or {}),
            checked=checked,
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
            key: step for key, step in self.steps.items() if key[1] != entry_id and entry_id not in key[0]
        }
        self.revision += 1
        return entry

    def clear(self) -> None:
        self.entries = []
        self.steps = {}
        self.revision += 1

    def applied_count(self, cursor: int) -> int:
        """Сколько первых событий применено к плану на время cursor (события ровно в cursor применены)."""
        return bisect.bisect_right([entry.event.time for entry in self.entries], cursor)

    def walk(self, base: PlanningSession, count: int | None = None) -> Walk:
        """Проходит первые count событий (все, если count не задан) по кэшу, пока шаги посчитаны."""
        limit = len(self.entries) if count is None else min(count, len(self.entries))
        session, prefix = base, ()
        steps: list[TimelineStep] = []
        keys: list[StepKey] = []
        for entry in self.entries[:limit]:
            key = (prefix, entry.id)
            step = self.steps.get(key)
            if step is None:
                break
            steps.append(step)
            keys.append(key)
            session = step.session
            if step.applied is not None:
                prefix = (*prefix, entry.id)
        return Walk(session=session, prefix=prefix, steps=steps, keys=keys)

    def store(self, walk: Walk, entry: TimelineEntry, step: TimelineStep) -> None:
        """Сохраняет шаг события entry, следующего за проходом walk."""
        self.steps[(walk.prefix, entry.id)] = step

    def prune(self, walk: Walk) -> None:
        """Оставляет в кэше только шаги полного прохода: прочие после изменений таймлайна уже не понадобятся."""
        if walk.done == len(self.entries):
            self.steps = {key: self.steps[key] for key in walk.keys}

    def view(self, base: PlanningSession, cursor: int) -> tuple[list[TimelineView], bool]:
        """События для ответа API и признак, что все шаги посчитаны.

        Отклонённое событие видно сразу, как только посчитан его шаг, даже если оно позже текущего времени.
        Принятое событие не позже cursor применено, остальные ждут своего времени или пересчёта.
        """
        walk = self.walk(base)
        items: list[TimelineView] = []
        for k, entry in enumerate(self.entries):
            step = walk.steps[k] if k < walk.done else None
            if step is not None and step.reason is not None:
                items.append(TimelineView(entry, entry.event, "rejected", step.reason))
            elif step is not None and step.applied is not None and entry.event.time <= cursor:
                items.append(TimelineView(entry, step.applied.event, "applied"))
            else:
                items.append(TimelineView(entry, entry.event, "pending"))
        return items, walk.done == len(self.entries)
