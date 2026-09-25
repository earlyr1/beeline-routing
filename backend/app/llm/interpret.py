"""Проверка ответа модели без участия модели: разбор аргументов, поиск id, конфликт с состоянием дня.

Ни одно предложение отсюда не меняет план: результат идёт в хранилище предложений.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, cast

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.domain.enums import (
    LEGACY_FOOT,
    TRANSPORT_RU,
    EventType,
    Priority,
    Skill,
    Transport,
    request_label,
)
from app.domain.models import DELAY_RANGE_TEXT, MAX_DELAY_MIN, MIN_DELAY_MIN, Event, Request
from app.domain.timeutil import HHMM, fmt_hhmm
from app.domain.validation_text import validation_text
from app.domain.windows import slot_for, slots_text
from app.llm.client import LlmResult, ToolCall
from app.planning.facts import EDITABLE_REQUEST_FIELDS, EventRejected, window_order_text
from app.planning.session import PlanningContext, PlanningSession, check_event

NOTHING_FOUND = (
    "Не нашёл в сообщении изменений плана. Опишите, что случилось: отмена заявки, "
    "срочная заявка, изменение заявки, недоступность инженера, смена транспорта или задержка инженера."
)
# Возврат отменённой заявки убран из интерфейса и из инструментов помощника: передумать можно только в уведомлении
# сразу после отмены. На просьбу вернуть помощник честно говорит, что так нельзя, а не молчит.
RESTORE_UNSUPPORTED = (
    "Вернуть отменённую заявку нельзя: передумать можно только в первые 5 секунд после отмены, в уведомлении. "
    "Если клиент снова ждёт визит, добавьте срочную заявку."
)
# Инструмент возврата, которого больше нет: модель со старой памятью ещё может его назвать.
LEGACY_RESTORE_TOOL = "propose_restore"
_RESTORE_WORDS = re.compile(
    r"\bверн(?:и|ите|уть|ём|ем|ёт|ет|ул|ула|ули)\b|снова в силе|\bвозврат", re.IGNORECASE
)
URGENT_WINDOW_MISSING = "Укажите окно визита или отметьте, что заявка как можно скорее."


def _norm(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


class _TimedArgs(BaseModel):
    time: HHMM | None = None
    rationale: str = ""


class RequestArgs(_TimedArgs):
    request_id: str = Field(min_length=1)


class EngineerArgs(_TimedArgs):
    engineer_id: str = Field(min_length=1)


# Требование заявки к транспорту в ответе модели: код или none. Прежний «foot» модель ещё может прислать, он читается
# как общественный транспорт (Transport("foot") == Transport.PUBLIC), но в схеме инструментов его нет.
TransportRequired = Literal["car", "bike", "public", "foot", "none"]
# Прежние названия, которыми модель может назвать общественный транспорт и пешком: до 16.09.2026 это были два типа,
# «Пешеход» (foot) и «Общественный транспорт».
LEGACY_PUBLIC_NAMES = frozenset({LEGACY_FOOT, "пешеход", "пешком", "общественный транспорт"})


class TransportChangeArgs(EngineerArgs):
    transport: Transport

    @field_validator("transport", mode="before")
    @classmethod
    def _known_transport(cls, value: object) -> Transport:
        """Модель иногда пишет транспорт по-русски или заглавными: принимаем код, русское и прежнее название."""
        if isinstance(value, str):
            key = _norm(value)
            for transport in Transport:
                if key in (transport.value, _norm(TRANSPORT_RU[transport])):
                    return transport
            if key in LEGACY_PUBLIC_NAMES:
                return Transport.PUBLIC
        allowed = ", ".join(transport.value for transport in Transport)
        raise ValueError(f"неизвестный тип транспорта «{value}», допустимы {allowed}")


class EngineerDelayArgs(EngineerArgs):
    delay_min: int

    @field_validator("delay_min")
    @classmethod
    def _delay_range(cls, value: int) -> int:
        if not MIN_DELAY_MIN <= value <= MAX_DELAY_MIN:
            raise ValueError(DELAY_RANGE_TEXT)
        return value


class UrgentArgs(_TimedArgs):
    address: str = Field(min_length=3)
    asap: bool = False
    window_start: HHMM | None = None
    window_end: HHMM | None = None
    duration_min: int = Field(gt=0, le=600)
    skill: Skill
    transport_required: TransportRequired | None = None

    @field_validator("asap", mode="before")
    @classmethod
    def _asap_null(cls, value: object) -> object:
        """Модель иногда передаёт null вместо false."""
        return False if value is None else value


class RequestUpdateArgs(RequestArgs):
    address: str | None = Field(default=None, min_length=3)
    window_start: HHMM | None = None
    window_end: HHMM | None = None
    duration_min: int | None = Field(default=None, gt=0, le=600)
    skill: Skill | None = None
    priority: Priority | None = None
    transport_required: TransportRequired | None = None
    asap: bool | None = None


class ClarifyArgs(BaseModel):
    question: str = Field(min_length=1)


ARGUMENT_MODELS: dict[str, type[_TimedArgs] | type[ClarifyArgs]] = {
    "propose_urgent_request": UrgentArgs,
    "propose_cancel": RequestArgs,
    "propose_engineer_unavailable": EngineerArgs,
    "propose_engineer_transport_change": TransportChangeArgs,
    "propose_request_update": RequestUpdateArgs,
    "propose_engineer_delay": EngineerDelayArgs,
    "ask_clarification": ClarifyArgs,
}


@dataclass
class ProposalDraft:
    event: Event
    rationale: str
    error: str | None = None


@dataclass
class Interpretation:
    drafts: list[ProposalDraft] = field(default_factory=list)
    clarifications: list[str] = field(default_factory=list)


class Unresolved(ValueError):
    """Модель назвала инженера или заявку, которых нельзя однозначно найти, или не сказала, что менять."""


class Refused(ValueError):
    """Предложение понятно, но заведомо не применится: черновик сохраняется с этой ошибкой."""

    def __init__(self, event: Event, text: str) -> None:
        super().__init__(text)
        self.event = event


def resolve_engineer(session: PlanningSession, value: str) -> str:
    if session.engineer(value) is not None:
        return value
    key = _norm(value)
    matches = [engineer for engineer in session.engineers if key and key in _norm(engineer.name)]
    if len(matches) == 1:
        return matches[0].id
    if not matches:
        raise Unresolved(f"Инженер «{value}» не найден.")
    names = ", ".join(engineer.name for engineer in matches[:5])
    raise Unresolved(f"Под «{value}» подходят несколько инженеров: {names}. Уточните, кого вы имеете в виду.")


# Во фронте срочная заявка подписана «URG-<номер>», в том числе заявка дня со своим билайновским номером.
URGENT_LABEL = re.compile(r"urg\s*-\s*(.+)", re.IGNORECASE)


def resolve_request(session: PlanningSession, value: str) -> str:
    if session.request(value) is not None:
        return value
    label = URGENT_LABEL.fullmatch(value.strip())
    if label is not None:
        # Диспетчер называет заявку так, как её подписал фронт: номер срочной заявки от подписи не меняется.
        number = label.group(1).strip()
        if session.request(number) is not None:
            return number
    key = _norm(value)
    matches = [request for request in session.requests if key and key in _norm(request.address)]
    if len(matches) == 1:
        return matches[0].id
    if not matches:
        raise Unresolved(f"Заявка «{value}» не найдена.")
    listed = "; ".join(
        f"{request_label(request.id, request.priority)} ({request.address})" for request in matches[:5]
    )
    raise Unresolved(f"Под «{value}» подходят несколько заявок: {listed}. Уточните номер.")


def _validation_text(error: ValidationError) -> str:
    return validation_text(error.errors(), limit=1)


def _on_grid(ctx: PlanningContext, window_start: int, window_end: int, notes: list[str]) -> tuple[int, int]:
    """Окно, которое назвала модель, на сетке окон: клиенту называют слот, а не произвольный интервал.

    Слот берётся по началу окна (app/domain/windows.py), поэтому «с часу до четырёх» становится слотом ещё до
    того, как из предложения соберётся событие, — но не молча: сдвиг уходит строкой в предложение, и диспетчер
    видит в карточке и то, о чём просили, и то, что он подтверждает. Время вне рабочего дня своего слота не имеет,
    и ближайший слот означал бы совсем другое окно (с 22:00 до 23:00 уехало бы назад, в 20:00–22:00): про такое
    окно помощник переспрашивает. Сетки в контексте нет — окно как есть.
    """
    slot = slot_for(ctx.window_grid, window_start)
    if slot is None:
        return window_start, window_end
    named = f"{fmt_hhmm(window_start)}–{fmt_hhmm(window_end)}"
    if not slot.start <= window_start < slot.end:
        raise Unresolved(
            f"Окна {named} в сетке нет: клиенту называют слот. Выберите один из: {slots_text(ctx.window_grid)}."
        )
    if (slot.start, slot.end) != (window_start, window_end):
        notes.append(f"Окно {named} положено на слот {fmt_hhmm(slot.start)}–{fmt_hhmm(slot.end)}.")
    return slot.start, slot.end


def _updated_request(
    stored: Request, args: RequestUpdateArgs, ctx: PlanningContext, notes: list[str]
) -> Request:
    """Текущая заявка с полями, которые назвала модель. У нового адреса координаты сбрасываются: их найдёт геокодер."""
    changes = args.model_dump(include=set(EDITABLE_REQUEST_FIELDS), exclude_none=True)
    if not changes:
        raise Unresolved(
            f"Не понял, что изменить в заявке {request_label(stored.id, stored.priority)}. "
            "Уточните окно, длительность, адрес или другое поле."
        )
    if changes.get("asap"):
        # Окно заявки «как можно скорее» задаёт backend, названное окно не используется.
        changes.pop("window_start", None)
        changes.pop("window_end", None)
    elif stored.asap and "asap" not in changes and {"window_start", "window_end"} & changes.keys():
        # Названное окно означает, что заявка больше не «как можно скорее».
        changes["asap"] = False
    if {"window_start", "window_end"} & changes.keys() and not changes.get("asap", stored.asap):
        # Новое окно кладём на сетку до того, как соберётся событие: диспетчеру предлагают слот.
        changes["window_start"], changes["window_end"] = _on_grid(
            ctx,
            changes.get("window_start", stored.window_start),
            changes.get("window_end", stored.window_end),
            notes,
        )
    elif changes.get("asap") is False and stored.asap:
        # Заявка перестала быть «как можно скорее»: окно ей задал сервер, слотом оно не бывает, и клиенту его
        # не называли. Берём слот, в который попадает начало ожидания, — иначе обещанием стало бы окно сервера.
        slot = slot_for(ctx.window_grid, stored.window_start)
        if slot is not None:
            changes["window_start"], changes["window_end"] = slot.start, slot.end
            notes.append(
                f"Заявка больше не «как можно скорее»: окно {fmt_hhmm(slot.start)}–{fmt_hhmm(slot.end)} с сетки."
            )
    if "transport_required" in changes:
        required = changes["transport_required"]
        changes["transport_required"] = None if required == "none" else Transport(required)
    if "address" in changes:
        changes["address"] = changes["address"].strip()
        if changes["address"] != stored.address:
            changes.update(lat=None, lon=None)
    return stored.model_copy(update=changes)


def _request_update_event(
    session: PlanningSession, args: RequestUpdateArgs, time: int, ctx: PlanningContext, notes: list[str]
) -> Event:
    request_id = resolve_request(session, args.request_id)
    stored = session.request(request_id)
    assert stored is not None  # resolve_request вернул номер заявки, которая есть в дне
    request = _updated_request(stored, args, ctx, notes)
    # Окно заявки «как можно скорее» задаёт backend при проверке события, порядок границ здесь не важен.
    if request.asap or request.window_end > request.window_start:
        return Event(type=EventType.REQUEST_UPDATED, time=time, request_id=request_id, request=request)
    if request.window_end < request.window_start:
        # Заявку с концом окна раньше начала нельзя записать даже в черновик: в нём остаётся прежнее окно.
        request = request.model_copy(
            update={"window_start": stored.window_start, "window_end": stored.window_end}
        )
    event = Event(type=EventType.REQUEST_UPDATED, time=time, request_id=request_id, request=request)
    raise Refused(event, window_order_text(request_label(stored.id, stored.priority)))


def _build_event(
    name: str,
    args: _TimedArgs,
    session: PlanningSession,
    ctx: PlanningContext,
    new_request_id: Callable[[], str],
    now: int,
    notes: list[str],
) -> Event:
    """notes — что помощник сделал с окном сверх сказанного моделью: эти строки уходят в пояснение предложения."""
    time = args.time if args.time is not None else now
    # Модель аргументов выбрана по имени инструмента (ARGUMENT_MODELS), и по нему же здесь известен её класс.
    if name == "propose_request_update":
        return _request_update_event(session, cast(RequestUpdateArgs, args), time, ctx, notes)
    if name == "propose_cancel":
        request_id = resolve_request(session, cast(RequestArgs, args).request_id)
        return Event(type=EventType.CANCEL, time=time, request_id=request_id)
    if name == "propose_engineer_unavailable":
        return Event(
            type=EventType.ENGINEER_UNAVAILABLE,
            time=time,
            engineer_id=resolve_engineer(session, cast(EngineerArgs, args).engineer_id),
        )
    if name == "propose_engineer_transport_change":
        change = cast(TransportChangeArgs, args)
        return Event(
            type=EventType.ENGINEER_TRANSPORT_CHANGED,
            time=time,
            engineer_id=resolve_engineer(session, change.engineer_id),
            transport=change.transport,
        )
    if name == "propose_engineer_delay":
        delay = cast(EngineerDelayArgs, args)
        return Event(
            type=EventType.ENGINEER_DELAYED,
            time=time,
            engineer_id=resolve_engineer(session, delay.engineer_id),
            delay_min=delay.delay_min,
        )
    urgent = cast(UrgentArgs, args)
    # Срочная заявка без названного окна — «как можно скорее», даже если модель забыла asap=true: так YandexGPT
    # отвечал на «Срочно авария на …». Переспрашиваем, только если названа одна граница окна.
    no_window = urgent.window_start is None and urgent.window_end is None
    if urgent.asap or no_window:
        # Окно заявки «как можно скорее» заполнит проверка события: от времени события до конца смен.
        window_start = window_end = time
    elif urgent.window_start is None or urgent.window_end is None:
        raise Unresolved(URGENT_WINDOW_MISSING)
    else:
        # Окно срочной заявки кладём на сетку до сборки события: клиенту называют слот.
        window_start, window_end = _on_grid(ctx, urgent.window_start, urgent.window_end, notes)
    transport = None if urgent.transport_required in (None, "none") else Transport(urgent.transport_required)
    request = Request(
        id=new_request_id(),
        address=urgent.address.strip(),
        duration_min=urgent.duration_min,
        window_start=window_start,
        window_end=window_end,
        priority=Priority.URGENT,
        asap=bool(urgent.asap) or no_window,
        skill=urgent.skill,
        transport_required=transport,
        source_type_bk="Срочная заявка из чата",
    )
    return Event(type=EventType.URGENT, time=time, request=request)


def _interpret_call(
    call: ToolCall,
    session: PlanningSession,
    ctx: PlanningContext,
    new_request_id: Callable[[], str],
    out: Interpretation,
    seen: set[tuple],
    now: int,
) -> None:
    if call.name == LEGACY_RESTORE_TOOL:
        out.clarifications.append(RESTORE_UNSUPPORTED)
        return
    model = ARGUMENT_MODELS.get(call.name)
    if model is None:
        out.clarifications.append(
            f"Помощник предложил неизвестное действие «{call.name}». Переформулируйте запрос."
        )
        return
    if call.arguments is None:
        out.clarifications.append(f"Не удалось разобрать предложение «{call.name}»: {call.error}.")
        return
    try:
        args = model.model_validate(call.arguments)
    except ValidationError as error:
        out.clarifications.append(
            f"Не удалось разобрать предложение «{call.name}»: {_validation_text(error)}."
        )
        return
    if isinstance(args, ClarifyArgs):
        out.clarifications.append(args.question.strip())
        return
    refusal: str | None = None
    notes: list[str] = []
    try:
        event = _build_event(call.name, args, session, ctx, new_request_id, now, notes)
    except Refused as error:
        event, refusal = error.event, str(error)
    except Unresolved as error:
        out.clarifications.append(str(error))
        return
    except ValidationError as error:
        out.clarifications.append(
            f"Не удалось разобрать предложение «{call.name}»: {_validation_text(error)}."
        )
        return

    request = event.request
    if request is None:
        described = None
    elif event.type == EventType.URGENT:
        described = request.address  # номер срочной заявки у каждого вызова новый
    else:
        described = request.model_dump_json()
    key = (
        event.type,
        event.request_id,
        event.engineer_id,
        event.transport,
        event.delay_min,
        event.time,
        described,
        refusal,
    )
    if key in seen:
        return
    seen.add(key)

    # Что помощник сделал с окном сам, диспетчер читает там же, где пояснение модели: в карточке предложения.
    rationale = " ".join([args.rationale.strip() or "Помощник не пояснил предложение.", *notes])
    if refusal is not None:
        out.drafts.append(ProposalDraft(event=event, rationale=rationale, error=refusal))
        return
    try:
        stored = check_event(session, event, ctx)
    except EventRejected as error:
        out.drafts.append(ProposalDraft(event=event, rationale=rationale, error=str(error)))
        return
    if (
        stored.type == EventType.URGENT
        and stored.request is not None
        and (stored.request.lat is None or stored.request.lon is None)
    ):
        out.drafts.append(
            ProposalDraft(
                event=stored,
                rationale=rationale,
                error=f"Адрес «{stored.request.address}» не найден на карте. Уточните адрес или добавьте заявку вручную с точкой на карте.",
            )
        )
        return
    out.drafts.append(ProposalDraft(event=stored, rationale=rationale))


def nothing_found(text: str) -> str:
    """Ответ, когда помощник не нашёл ни изменений, ни вопроса: просьба вернуть заявку получает честный отказ."""
    return RESTORE_UNSUPPORTED if _RESTORE_WORDS.search(text) else NOTHING_FOUND


def interpret(
    result: LlmResult,
    session: PlanningSession,
    ctx: PlanningContext,
    new_request_id: Callable[[], str],
    now: int | None = None,
) -> Interpretation:
    """now — текущее время плана на шкале: время события, если модель его не назвала. Без него время сессии."""
    out = Interpretation()
    seen: set[tuple] = set()
    for call in result.calls:
        _interpret_call(call, session, ctx, new_request_id, out, seen, session.now if now is None else now)
    if not result.calls and result.text and result.text.strip():
        out.clarifications.append(result.text.strip())
    return out
