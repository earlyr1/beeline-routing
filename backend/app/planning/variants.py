"""Варианты исправления плана на «ломающее» событие дня: стратегии, сравнение итогов и рекомендация.

Спецификация: docs/superpowers/specs/2026-09-17-event-variants-design.md. «Ломающие» события — те, после которых
есть разные способы спасти день: недоступность, смена транспорта, задержка инженера, срочная заявка и
переназначение заявки диспетчером. Отмена, возврат и изменение заявки применяются одним планом, как раньше.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from app.domain.enums import TRANSPORT_RU, EventType, Priority, ReasonCode
from app.domain.models import Event, Plan, Request, Unassigned, Visit
from app.planning.models import EventChoice, EventVariant, PlanDiff, VariantOption
from app.solvers.assemble import build_plan
from app.solvers.problem import EngineerState, Problem
from app.solvers.simulate import simulate_route

VARIANTS: tuple[EventVariant, ...] = ("optimal", "stable", "keep")
BREAKING_EVENTS = frozenset(
    {
        EventType.URGENT,
        EventType.ENGINEER_UNAVAILABLE,
        EventType.ENGINEER_TRANSPORT_CHANGED,
        EventType.ENGINEER_DELAYED,
        EventType.REQUEST_REASSIGNED,
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
# У переназначения заявки «keep» не оставляет всё как есть, а вставляет заявку в маршрут выбранной бригады.
EVENT_VARIANT_TITLES: dict[EventType, dict[EventVariant, str]] = {
    EventType.REQUEST_REASSIGNED: {"keep": "Вставить в маршрут"},
}
EVENT_VARIANT_SUMMARIES: dict[EventType, dict[EventVariant, str]] = {
    EventType.REQUEST_REASSIGNED: {
        "keep": "Бригада пропускает, на что не успевает, остальные маршруты как есть"
    },
}
KEEP_TEXT = "Вариант «Ничего не менять»: план не пересчитан, заявку никто не забрал."
INSERT_TITLE = EVENT_VARIANT_TITLES[EventType.REQUEST_REASSIGNED]["keep"]
INSERT_TEXT = f"Вариант «{INSERT_TITLE}»: план не пересчитан, заявку никто не забрал."
MAX_LINES = 3


def is_choosable(event: Event) -> bool:
    return event.type in BREAKING_EVENTS


def variant_title(variant: EventVariant, event_type: EventType) -> str:
    return EVENT_VARIANT_TITLES.get(event_type, {}).get(variant, VARIANT_TITLES[variant])


def variant_summary(variant: EventVariant, event_type: EventType) -> str:
    return EVENT_VARIANT_SUMMARIES.get(event_type, {}).get(variant, VARIANT_SUMMARIES[variant])


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
    sequences, fixed = _previous_routes(problem, KEEP_TEXT)
    return _routes_plan(problem, sequences, fixed, unassigned_before, KEEP_TEXT)


def insert_plan(
    problem: Problem, request_id: str, engineer_id: str, unassigned_before: Collection[str] = ()
) -> Plan:
    """«Вставить в маршрут» для переназначения заявки: без решателя, меняется только маршрут выбранной бригады.

    Заявка request_id уходит из прежнего маршрута и встаёт в оставшийся маршрут бригады engineer_id. Для каждого
    места вставки маршрут идёт по порядку «заявки до места, новая заявка, заявки после места». Бригада берёт новую
    заявку и добавляет к ней свои: сначала закреплённые диспетчером, затем срочные, затем обычные, в каждой группе —
    по прежнему порядку. Заявка берётся, если маршрут со всеми взятыми остаётся не хуже прежнего (_no_worse), иначе
    бригада её пропускает. Из мест берётся то, где пропущено меньше закреплённых заявок, затем срочных, затем всех,
    затем короче маршрут; при равенстве — место раньше. Пропущенные заявки остаются без инженера. Маршруты
    остальных бригад прежние, заявки без инженера до события сохраняют свою причину, как в keep_plan.
    """
    routes, fixed = _previous_routes(problem, INSERT_TEXT)
    sequences = {eid: [rid for rid in sequence if rid != request_id] for eid, sequence in routes.items()}
    fixed.pop(request_id, None)
    state = problem.state(engineer_id)
    route = sequences.get(engineer_id, [])
    fits = _no_worse(problem, state, route)
    # sorted устойчив: внутри группы остаётся прежний порядок.
    by_importance = sorted(route, key=lambda rid: _importance(problem.request(rid)))
    best: tuple[tuple[int, int, int, float], list[str], list[str]] | None = None
    for position in range(len(route) + 1) if fits([request_id]) else ():
        sequence = [*route[:position], request_id, *route[position:]]
        taken = {request_id}
        for candidate in by_importance:
            if fits([rid for rid in sequence if rid in taken or rid == candidate]):
                taken.add(candidate)
        kept = [rid for rid in sequence if rid in taken]
        skipped = [rid for rid in route if rid not in taken]
        requests = [problem.request(rid) for rid in skipped]
        key = (
            sum(1 for request in requests if request.fixed_engineer_id is not None),
            sum(1 for request in requests if request.priority == Priority.URGENT),
            len(skipped),
            sum(visit.leg_km for visit in simulate_route(problem, state, kept).visits),
        )
        if best is None or key < best[0]:
            best = (key, kept, skipped)
    if best is not None:
        _, kept, skipped = best
        sequences[engineer_id] = kept
        text = f"Вариант «{INSERT_TITLE}»: {state.engineer.name} пропускает заявку, чтобы успеть к заявке {request_id}."
        for rid in skipped:
            fixed[rid] = Unassigned(request_id=rid, reason_code=ReasonCode.NO_FREE_ENGINEER, reason_text=text)
    # Если заявку не вставить никуда, её причину считает build_plan.
    return _routes_plan(problem, sequences, fixed, [*unassigned_before, request_id], INSERT_TEXT)


def _importance(request: Request) -> int:
    """Очередь заявки при вставке: закреплённая диспетчером — 0, срочная — 1, обычная — 2. Меньше — берётся раньше."""
    if request.fixed_engineer_id is not None:
        return 0
    return 1 if request.priority == Priority.URGENT else 2


def _no_worse(
    problem: Problem, state: EngineerState, route: Sequence[str]
) -> Callable[[Sequence[str]], bool]:
    """Проверка маршрута бригады при вставке: не хуже ли он прежнего маршрута route.

    Допустимый маршрут подходит. Недопустимый подходит, если нарушений не прибавилось: ни одна заявка не опаздывает
    и не заканчивается после смены больше, чем в прежнем маршруте (новая заявка — вовремя и в смену), а обед, если в
    прежнем маршруте он помещался, не пропадает и не перестаёт помещаться. Визиты сравниваются в том виде, в каком их
    покажет план, — обычной симуляцией. Так бригада не пропускает заявку, к которой опаздывала и без вставки.
    """
    before = simulate_route(problem, state, route)
    allowed = {visit.request_id: (visit.late_min, _overtime(visit, state)) for visit in before.visits}

    def fits(request_ids: Sequence[str]) -> bool:
        result = simulate_route(problem, state, request_ids)
        if result.feasible:
            return True
        lunch_lost = result.lunch_conflict or (result.lunch is None and before.lunch is not None)
        if lunch_lost and not before.lunch_conflict:
            return False
        return all(
            visit.late_min <= allowed.get(visit.request_id, (0, 0))[0]
            and _overtime(visit, state) <= allowed.get(visit.request_id, (0, 0))[1]
            for visit in result.visits
        )

    return fits


def _overtime(visit: Visit, state: EngineerState) -> int:
    return max(0, visit.end - state.available_until)


def _previous_routes(problem: Problem, text: str) -> tuple[dict[str, list[str]], dict[str, Unassigned]]:
    """Прежние маршруты на задаче после события и заявки, которые из них выпадают, с причиной варианта text.

    Выпадают заявки инженера, которому больше нельзя работать, и заявки, которым нужен транспорт, которого у
    инженера теперь нет.
    """
    open_ids = set(problem.open_request_ids)
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
                    request_id=request_id, reason_code=ReasonCode.NO_FREE_ENGINEER, reason_text=text
                )
            elif request.transport_required is not None and request.transport_required != engineer.transport:
                fixed[request_id] = Unassigned(
                    request_id=request_id,
                    reason_code=ReasonCode.NO_TRANSPORT,
                    reason_text=(
                        f"{text} Нужен транспорт «{TRANSPORT_RU[request.transport_required]}», "
                        f"у {engineer.name} «{TRANSPORT_RU[engineer.transport]}»."
                    ),
                )
            else:
                kept.append(request_id)
        sequences[engineer.id] = kept
    return sequences, fixed


def _routes_plan(
    problem: Problem,
    sequences: dict[str, list[str]],
    fixed: dict[str, Unassigned],
    unassigned_before: Collection[str],
    text: str,
) -> Plan:
    """План варианта без решателя. Открытые заявки вне маршрутов получают причину text, кроме бывших без инженера."""
    before = set(unassigned_before)
    placed = {request_id for sequence in sequences.values() for request_id in sequence}
    for request_id in problem.open_request_ids:
        if request_id not in placed and request_id not in fixed and request_id not in before:
            fixed[request_id] = Unassigned(
                request_id=request_id, reason_code=ReasonCode.NO_FREE_ENGINEER, reason_text=text
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
            title=variant_title(outcome.variant, event.type),
            summary=variant_summary(outcome.variant, event.type),
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
