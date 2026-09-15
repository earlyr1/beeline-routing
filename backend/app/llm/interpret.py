"""Проверка ответа модели без участия модели: разбор аргументов, поиск id, конфликт с состоянием дня.

Ни одно предложение отсюда не меняет план: результат идёт в хранилище предложений.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.domain.enums import TRANSPORT_RU, EventType, Priority, Skill, Transport
from app.domain.models import Event, Request
from app.domain.timeutil import HHMM
from app.llm.client import LlmResult, ToolCall
from app.planning.session import EventRejected, PlanningContext, PlanningSession, check_event

NOTHING_FOUND = (
    "Не нашёл в сообщении изменений плана. Опишите, что случилось: отмена или возврат заявки, "
    "срочная заявка, недоступность инженера или смена транспорта."
)


def _norm(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


class _TimedArgs(BaseModel):
    time: HHMM | None = None
    rationale: str = ""


class RequestArgs(_TimedArgs):
    request_id: str = Field(min_length=1)


class EngineerArgs(_TimedArgs):
    engineer_id: str = Field(min_length=1)


class TransportChangeArgs(EngineerArgs):
    transport: Transport

    @field_validator("transport", mode="before")
    @classmethod
    def _known_transport(cls, value: object) -> Transport:
        """Модель иногда пишет транспорт по-русски или заглавными: принимаем код и русское название."""
        if isinstance(value, str):
            key = _norm(value)
            for transport in Transport:
                if key in (transport.value, _norm(TRANSPORT_RU[transport])):
                    return transport
        allowed = ", ".join(transport.value for transport in Transport)
        raise ValueError(f"неизвестный тип транспорта «{value}», допустимы {allowed}")


class UrgentArgs(_TimedArgs):
    address: str = Field(min_length=3)
    window_start: HHMM
    window_end: HHMM
    duration_min: int = Field(gt=0, le=600)
    skill: Skill
    transport_required: Literal["car", "foot", "bike", "public", "none"] | None = None


class ClarifyArgs(BaseModel):
    question: str = Field(min_length=1)


ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "propose_urgent_request": UrgentArgs,
    "propose_cancel": RequestArgs,
    "propose_restore": RequestArgs,
    "propose_engineer_unavailable": EngineerArgs,
    "propose_engineer_transport_change": TransportChangeArgs,
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
    """Модель назвала инженера или заявку, которых нельзя однозначно найти."""


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


def resolve_request(session: PlanningSession, value: str) -> str:
    if session.request(value) is not None:
        return value
    key = _norm(value)
    matches = [request for request in session.requests if key and key in _norm(request.address)]
    if len(matches) == 1:
        return matches[0].id
    if not matches:
        raise Unresolved(f"Заявка «{value}» не найдена.")
    listed = "; ".join(f"{request.id} ({request.address})" for request in matches[:5])
    raise Unresolved(f"Под «{value}» подходят несколько заявок: {listed}. Уточните номер.")


def _validation_text(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"])
    message = str(first["msg"]).removeprefix("Value error, ")
    return f"{location}: {message}" if location else message


def _build_event(
    name: str, args: BaseModel, session: PlanningSession, new_request_id: Callable[[], str]
) -> Event:
    time = args.time if args.time is not None else session.now
    if name == "propose_cancel":
        return Event(type=EventType.CANCEL, time=time, request_id=resolve_request(session, args.request_id))
    if name == "propose_restore":
        return Event(type=EventType.RESTORE, time=time, request_id=resolve_request(session, args.request_id))
    if name == "propose_engineer_unavailable":
        return Event(
            type=EventType.ENGINEER_UNAVAILABLE,
            time=time,
            engineer_id=resolve_engineer(session, args.engineer_id),
        )
    if name == "propose_engineer_transport_change":
        return Event(
            type=EventType.ENGINEER_TRANSPORT_CHANGED,
            time=time,
            engineer_id=resolve_engineer(session, args.engineer_id),
            transport=args.transport,
        )
    transport = None if args.transport_required in (None, "none") else Transport(args.transport_required)
    request = Request(
        id=new_request_id(),
        address=args.address.strip(),
        duration_min=args.duration_min,
        window_start=args.window_start,
        window_end=args.window_end,
        priority=Priority.URGENT,
        skill=args.skill,
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
) -> None:
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
    try:
        event = _build_event(call.name, args, session, new_request_id)
    except Unresolved as error:
        out.clarifications.append(str(error))
        return
    except ValidationError as error:
        out.clarifications.append(
            f"Не удалось разобрать предложение «{call.name}»: {_validation_text(error)}."
        )
        return

    request = event.request
    key = (
        event.type,
        event.request_id,
        event.engineer_id,
        event.transport,
        event.time,
        request.address if request else None,
    )
    if key in seen:
        return
    seen.add(key)

    rationale = args.rationale.strip() or "Помощник не пояснил предложение."
    try:
        stored = check_event(session, event, ctx)
    except EventRejected as error:
        out.drafts.append(ProposalDraft(event=event, rationale=rationale, error=str(error)))
        return
    if stored.request is not None and (stored.request.lat is None or stored.request.lon is None):
        out.drafts.append(
            ProposalDraft(
                event=stored,
                rationale=rationale,
                error=f"Адрес «{stored.request.address}» не найден на карте. Уточните адрес или добавьте заявку вручную с точкой на карте.",
            )
        )
        return
    out.drafts.append(ProposalDraft(event=stored, rationale=rationale))


def interpret(
    result: LlmResult,
    session: PlanningSession,
    ctx: PlanningContext,
    new_request_id: Callable[[], str],
) -> Interpretation:
    out = Interpretation()
    seen: set[tuple] = set()
    for call in result.calls:
        _interpret_call(call, session, ctx, new_request_id, out, seen)
    if not result.calls and result.text and result.text.strip():
        out.clarifications.append(result.text.strip())
    return out
