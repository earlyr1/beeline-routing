"""Варианты исправления плана на «ломающее» событие дня: стратегии, сравнение итогов и рекомендация.

Спецификация: docs/superpowers/specs/2026-09-17-event-variants-design.md. «Ломающие» события — те, после которых
есть разные способы спасти день: недоступность, смена транспорта, задержка инженера и срочная заявка. Отмена,
возврат и изменение заявки применяются одним планом, как раньше.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from app.domain.enums import TRANSPORT_RU, EventType, ReasonCode
from app.domain.models import Event, Plan, Unassigned
from app.planning.models import EventChoice, EventVariant, PlanDiff, VariantOption
from app.solvers.assemble import build_plan
from app.solvers.problem import Problem

VARIANTS: tuple[EventVariant, ...] = ("optimal", "stable", "keep")
BREAKING_EVENTS = frozenset(
    {
        EventType.URGENT,
        EventType.ENGINEER_UNAVAILABLE,
        EventType.ENGINEER_TRANSPORT_CHANGED,
        EventType.ENGINEER_DELAYED,
    }
)
# «Минимум перестановок»: условные 500 км за перенос заявки к другому инженеру вместо 20. Снять заявку всё равно
# дороже (drop_normal), поэтому заявки пострадавшей бригады уходят другим, а чужие маршруты почти не трогаются.
STABLE_REASSIGNMENT = 500_000
VARIANT_TITLES: dict[EventVariant, str] = {
    "optimal": "Оптимально по дню",
    "stable": "Минимум перестановок",
    "keep": "Ничего не менять",
}
VARIANT_SUMMARIES: dict[EventVariant, str] = {
    "optimal": "Пересчитать остаток дня целиком",
    "stable": "Чужие маршруты почти не трогаем",
    "keep": "Оставить маршруты как есть",
}
KEEP_TEXT = "Вариант «Ничего не менять»: план не пересчитан, заявку никто не забрал."
MAX_LINES = 3


def is_choosable(event: Event) -> bool:
    return event.type in BREAKING_EVENTS


def late_visits(plan: Plan) -> int:
    """Визиты плана, которые начнутся позже конца окна."""
    return sum(1 for route in plan.routes for visit in route.visits if visit.late_min > 0)


def keep_plan(problem: Problem, unassigned_before: Collection[str] = ()) -> Plan:
    """«Ничего не менять»: прежние маршруты без решателя на задаче после события.

    Порядок заявок инженера — его несделанная часть плана до события (Problem.previous_order из pin_problem).
    Маршруты прогоняются обычной симуляцией: опоздания и переработки видны в визитах и нарушениях. Заявки
    инженера, которому больше нельзя работать (недоступен или задержан до конца смены), и заявки, которым нужен
    транспорт, которого у инженера теперь нет, остаются без инженера. Новая срочная заявка ни в чей маршрут
    не попадает. unassigned_before — заявки без инженера в плане до события: их причину считает build_plan,
    как обычно, а не пишет этот вариант.
    """
    open_ids = set(problem.open_request_ids)
    before = set(unassigned_before)
    sequences: dict[str, list[str]] = {}
    fixed: dict[str, Unassigned] = {}
    for state in problem.states:
        engineer = state.engineer
        kept: list[str] = []
        for request_id in problem.previous_order.get(engineer.id, []):
            if request_id not in open_ids:
                continue
            request = problem.request(request_id)
            if state.available_from >= state.available_until:
                fixed[request_id] = Unassigned(
                    request_id=request_id, reason_code=ReasonCode.NO_FREE_ENGINEER, reason_text=KEEP_TEXT
                )
            elif request.transport_required is not None and request.transport_required != engineer.transport:
                fixed[request_id] = Unassigned(
                    request_id=request_id,
                    reason_code=ReasonCode.NO_TRANSPORT,
                    reason_text=(
                        f"{KEEP_TEXT} Нужен транспорт «{TRANSPORT_RU[request.transport_required]}», "
                        f"у {engineer.name} «{TRANSPORT_RU[engineer.transport]}»."
                    ),
                )
            else:
                kept.append(request_id)
        sequences[engineer.id] = kept
    placed = {request_id for sequence in sequences.values() for request_id in sequence}
    for request_id in problem.open_request_ids:
        if request_id not in placed and request_id not in fixed and request_id not in before:
            fixed[request_id] = Unassigned(
                request_id=request_id, reason_code=ReasonCode.NO_FREE_ENGINEER, reason_text=KEEP_TEXT
            )
    return build_plan(problem, "ortools", sequences, fixed_unassigned=fixed)


@dataclass(frozen=True)
class Outcome:
    """Итог одной стратегии: план после события и разница с планом до него."""

    variant: EventVariant
    plan: Plan
    diff: PlanDiff


def _clients(option: VariantOption) -> int:
    return option.metrics.unassigned + option.late


def _rank(option: VariantOption) -> tuple[int, int, int, float, int]:
    return (
        _clients(option),
        option.metrics.engineers_used,
        option.moved,
        round(option.metrics.total_km, 1),
        VARIANTS.index(option.variant),
    )


def _plural(count: int, one: str, few: str, many: str) -> str:
    mod10, mod100 = count % 10, count % 100
    if mod10 == 1 and mod100 != 11:
        return one
    if 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        return few
    return many


def _counted(delta: int, noun: Callable[[int], str], tail: str = "") -> tuple[int, str, str]:
    count = abs(delta)
    suffix = f" {tail}" if tail else ""
    return delta, f"на {count} {noun(count)} меньше{suffix}", f"на {count} {noun(count)} больше{suffix}"


def _clients_row(delta: int) -> tuple[int, str, str]:
    count = abs(delta)
    noun = _plural(count, "клиента", "клиента", "клиентов")
    return (
        delta,
        f"на {count} {noun} без инженера или с опозданием меньше",
        f"на {count} {noun} без инженера или с опозданием больше",
    )


def _compare(option: VariantOption, reference: VariantOption) -> tuple[list[str], list[str]]:
    """Чем вариант лучше и хуже reference: только ненулевые разницы, в порядке важности."""
    km = round(option.metrics.total_km - reference.metrics.total_km, 1)
    km_text = f"{abs(km):.1f}".replace(".", ",")
    rows = [
        _clients_row(_clients(option) - _clients(reference)),
        _counted(
            option.metrics.engineers_used - reference.metrics.engineers_used,
            lambda n: _plural(n, "бригаду", "бригады", "бригад"),
        ),
        _counted(
            option.moved - reference.moved,
            lambda n: _plural(n, "заявку", "заявки", "заявок"),
            "переезжает к другим бригадам",
        ),
    ]
    pros: list[str] = []
    cons: list[str] = []
    for delta, better, worse in rows:
        if delta < 0:
            pros.append(better)
        elif delta > 0:
            cons.append(worse)
    if km < 0:
        pros.append(f"на {km_text} км меньше")
    elif km > 0:
        cons.append(f"на {km_text} км больше")
    return pros[:MAX_LINES], cons[:MAX_LINES]


def build_choice(
    entry_id: str,
    event: Event,
    before: Plan,
    outcomes: Sequence[Outcome],
    current: EventVariant | None,
) -> EventChoice:
    """Варианты в порядке VARIANTS с рекомендацией и строками «лучше / хуже».

    Остальные варианты сравниваются с рекомендованным, рекомендованный — со следующим по ключу рекомендации.
    """
    options = [
        VariantOption(
            variant=outcome.variant,
            title=VARIANT_TITLES[outcome.variant],
            summary=VARIANT_SUMMARIES[outcome.variant],
            metrics=outcome.plan.metrics,
            late=late_visits(outcome.plan),
            moved=len(outcome.diff.moved),
        )
        for outcome in sorted(outcomes, key=lambda outcome: VARIANTS.index(outcome.variant))
    ]
    ranked = sorted(options, key=_rank)
    best = ranked[0]
    described = []
    for option in options:
        reference = ranked[1] if option is best and len(ranked) > 1 else best
        pros, cons = _compare(option, reference) if reference is not option else ([], [])
        described.append(
            option.model_copy(update={"pros": pros, "cons": cons, "recommended": option is best})
        )
    return EventChoice(
        entry_id=entry_id,
        event=event,
        metrics_before=before.metrics,
        late_before=late_visits(before),
        variants=described,
        current=current,
    )
