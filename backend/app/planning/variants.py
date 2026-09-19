"""Варианты исправления плана на «ломающее» событие дня: стратегии, сравнение итогов и рекомендация.

Спецификация: docs/superpowers/specs/2026-09-17-event-variants-design.md. «Ломающие» события — те, после которых
есть разные способы спасти день: недоступность, смена транспорта, задержка инженера, срочная заявка и
переназначение заявки диспетчером. Отмена, возврат и изменение заявки применяются одним планом, как раньше.

У срочной заявки к трём посчитанным заранее вариантам добавляется четвёртый — «отдать заявку названной
бригаде» (стратегия «assign:<инженер>»). Его считают по запросу диспетчера и сравнивают с «Оптимально по дню»:
так видно, чего стоит решение отдать заявку не туда, куда её кладёт оптимум.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass

from app.domain.enums import TRANSPORT_RU, EventType, ReasonCode, request_label
from app.domain.models import Event, Plan, Unassigned, Visit, dispatch_order
from app.planning.models import BaseVariant, EventChoice, EventVariant, PlanDiff, VariantOption
from app.solvers.assemble import build_plan
from app.solvers.problem import EngineerState, Problem
from app.solvers.simulate import simulate_route

VARIANTS: tuple[BaseVariant, ...] = ("optimal", "stable", "keep")
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
VARIANT_TITLES: dict[BaseVariant, str] = {
    "optimal": "Оптимально по дню",
    "stable": "Минимум перестановок",
    "keep": "Ничего не менять",
}
VARIANT_SUMMARIES: dict[BaseVariant, str] = {
    "optimal": "Пересчитать остаток дня целиком",
    "stable": "Чужие маршруты почти не трогаем",
    "keep": "Оставить маршруты как есть",
}
# У переназначения заявки «keep» не оставляет всё как есть, а вставляет заявку в маршрут выбранной бригады.
EVENT_VARIANT_TITLES: dict[EventType, dict[BaseVariant, str]] = {
    EventType.REQUEST_REASSIGNED: {"keep": "Вставить в маршрут"},
}
EVENT_VARIANT_SUMMARIES: dict[EventType, dict[BaseVariant, str]] = {
    EventType.REQUEST_REASSIGNED: {
        "keep": "Бригада пропускает, на что не успевает, остальные маршруты как есть"
    },
}
# Стратегия «отдать заявку бригаде»: «assign:E07». Бригада называется в заголовке варианта.
ASSIGN_PREFIX = "assign:"
ASSIGN_SUMMARY = "Выбор диспетчера"
KEEP_TEXT = "Вариант «Ничего не менять»: план не пересчитан, заявку никто не забрал."
INSERT_TITLE = EVENT_VARIANT_TITLES[EventType.REQUEST_REASSIGNED]["keep"]
INSERT_TEXT = f"Вариант «{INSERT_TITLE}»: план не пересчитан, заявку никто не забрал."
MAX_LINES = 3


def is_choosable(event: Event) -> bool:
    return event.type in BREAKING_EVENTS


def is_assignable(event: Event) -> bool:
    """Событие, у которого диспетчер может отдать заявку выбранной бригаде.

    Только срочная заявка: остальные «ломающие» события двигают целую пачку заявок, а переназначение само
    называет бригаду.
    """
    return event.type == EventType.URGENT


def assign_variant(engineer_id: str) -> EventVariant:
    """Стратегия «отдать заявку бригаде engineer_id»."""
    return f"{ASSIGN_PREFIX}{engineer_id}"


def assigned_engineer(variant: EventVariant | None) -> str | None:
    """Бригада стратегии «отдать заявку», если это она; иначе None (в том числе у «assign:» без номера)."""
    if variant is None or not variant.startswith(ASSIGN_PREFIX):
        return None
    return variant[len(ASSIGN_PREFIX) :] or None


def choice_request_id(event: Event) -> str | None:
    """Заявка, о которой событие, если оно об одной заявке: срочная и переназначение. Иначе None."""
    if event.type == EventType.URGENT:
        return event.request.id if event.request is not None else None
    if event.type == EventType.REQUEST_REASSIGNED:
        return event.request_id
    return None


def variant_title(
    variant: EventVariant, event_type: EventType, names: Mapping[str, str] | None = None
) -> str:
    """Название варианта для диспетчера; у «отдать бригаде» — имя бригады из names (иначе её номер)."""
    engineer_id = assigned_engineer(variant)
    if engineer_id is not None:
        return f"Отдать: {(names or {}).get(engineer_id, engineer_id)}"
    return EVENT_VARIANT_TITLES.get(event_type, {}).get(variant, VARIANT_TITLES[variant])


def variant_summary(variant: EventVariant, event_type: EventType) -> str:
    if assigned_engineer(variant) is not None:
        return ASSIGN_SUMMARY
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
    заявку и добавляет к ней свои по очереди распределения (dispatch_order): сначала закреплённые диспетчером,
    затем аварии и срочные, затем подключения, затем ремонт и дозаказ, в каждой группе — по прежнему порядку.
    Заявка берётся, если маршрут со всеми взятыми остаётся не хуже прежнего (_no_worse), иначе бригада её
    пропускает. Из мест берётся то, где пропущено меньше закреплённых заявок, затем аварий, затем подключений,
    затем всех, затем короче маршрут; при равенстве — место раньше. Пропущенные заявки остаются без инженера. Маршруты
    остальных бригад прежние, заявки без инженера до события сохраняют свою причину, как в keep_plan.
    """
    routes, fixed = _previous_routes(problem, INSERT_TEXT)
    sequences = {eid: [rid for rid in sequence if rid != request_id] for eid, sequence in routes.items()}
    fixed.pop(request_id, None)
    state = problem.state(engineer_id)
    route = sequences.get(engineer_id, [])
    fits = _no_worse(problem, state, route)
    # sorted устойчив: внутри группы остаётся прежний порядок.
    by_importance = sorted(route, key=lambda rid: dispatch_order(problem.request(rid)))
    best: tuple[tuple[int, int, int, int, float], list[str], list[str]] | None = None
    for position in range(len(route) + 1) if fits([request_id]) else ():
        sequence = [*route[:position], request_id, *route[position:]]
        taken = {request_id}
        for candidate in by_importance:
            if fits([rid for rid in sequence if rid in taken or rid == candidate]):
                taken.add(candidate)
        kept = [rid for rid in sequence if rid in taken]
        skipped = [rid for rid in route if rid not in taken]
        orders = [dispatch_order(problem.request(rid)) for rid in skipped]
        key = (
            orders.count(0),
            orders.count(1),
            orders.count(2),
            len(skipped),
            sum(visit.leg_km for visit in simulate_route(problem, state, kept).visits),
        )
        if best is None or key < best[0]:
            best = (key, kept, skipped)
    if best is not None:
        _, kept, skipped = best
        sequences[engineer_id] = kept
        label = request_label(request_id, problem.request(request_id).priority)
        text = f"Вариант «{INSERT_TITLE}»: {state.engineer.name} пропускает заявку, чтобы успеть к заявке {label}."
        # Заявку, на которую у бригады уже не осталось оборудования, она пропускает не из-за времени: взять
        # единицу днём негде, и «чтобы успеть» про неё было бы неправдой.
        no_equipment = state.equipment_left - problem.equipment_used(kept) <= 0
        short = (
            f"Вариант «{INSERT_TITLE}»: у {state.engineer.name} не осталось оборудования на эту заявку: "
            f"утром бригада взяла {state.engineer.equipment_stock} ед."
        )
        for rid in skipped:
            reason = short if no_equipment and problem.request(rid).needs_equipment else text
            fixed[rid] = Unassigned(
                request_id=rid, reason_code=ReasonCode.NO_FREE_ENGINEER, reason_text=reason
            )
    # Если заявку не вставить никуда, её причину считает build_plan.
    return _routes_plan(problem, sequences, fixed, [*unassigned_before, request_id], INSERT_TEXT)


def _no_worse(
    problem: Problem, state: EngineerState, route: Sequence[str]
) -> Callable[[Sequence[str]], bool]:
    """Проверка маршрута бригады при вставке: не хуже ли он прежнего маршрута route.

    Допустимый маршрут подходит. Недопустимый подходит, если нарушений не прибавилось: ни одна заявка не опаздывает
    и не заканчивается после смены больше, чем в прежнем маршруте (новая заявка — вовремя и в смену), не появилось
    плечо длиннее предела транспорта, не появилось заявки, на которую у бригады не осталось оборудования, а обед,
    если в прежнем маршруте он помещался, не пропадает и не перестаёт
    помещаться. Визиты сравниваются в том виде, в каком их покажет план, — обычной симуляцией. Так бригада не
    пропускает заявку, к которой опаздывала и без вставки, но и не едет на велосипеде через полобласти: по времени
    такая вставка проходит, а предел плеча она нарушает.
    """
    before = simulate_route(problem, state, route)
    allowed = {visit.request_id: (visit.late_min, _overtime(visit, state)) for visit in before.visits}
    long_before = _long_legs(problem, state, before.visits)
    without_equipment_before = _without_equipment(problem, state, route)

    def fits(request_ids: Sequence[str]) -> bool:
        result = simulate_route(problem, state, request_ids)
        if result.feasible:
            return True
        if _long_legs(problem, state, result.visits) - long_before:
            return False
        if _without_equipment(problem, state, request_ids) - without_equipment_before:
            return False
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


def _without_equipment(problem: Problem, state: EngineerState, request_ids: Sequence[str]) -> set[str]:
    """Заявки маршрута, на которые у бригады уже не осталось оборудования: новых единиц днём она не берёт."""
    used = 0
    short: set[str] = set()
    for request_id in request_ids:
        if problem.request(request_id).needs_equipment:
            used += 1
            if used > state.equipment_left:
                short.add(request_id)
    return short


def _long_legs(problem: Problem, state: EngineerState, visits: Sequence[Visit]) -> set[str]:
    """Заявки маршрута, до которых плечо длиннее предела транспорта бригады. У автомобиля предела нет."""
    node = state.start_node
    long_legs: set[str] = set()
    for visit in visits:
        destination = problem.request_node(visit.request_id)
        if problem.leg_too_long(node, destination, state.engineer):
            long_legs.add(visit.request_id)
        node = destination
    return long_legs


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


def _order(outcome: Outcome) -> int:
    """Порядок вариантов в окне: три базовых, затем «отдать бригаде»."""
    return VARIANTS.index(outcome.variant) if outcome.variant in VARIANTS else len(VARIANTS)


def _request_engineer(plan: Plan, request_id: str | None) -> str | None:
    """Бригада, у которой заявка в маршрутах плана; None — заявки в маршрутах нет."""
    if request_id is None:
        return None
    return next(
        (
            route.engineer_id
            for route in plan.routes
            for visit in route.visits
            if visit.request_id == request_id
        ),
        None,
    )


def build_choice(
    entry_id: str,
    event: Event,
    before: Plan,
    outcomes: Sequence[Outcome],
    current: EventVariant | None,
    names: Mapping[str, str] | None = None,
) -> EventChoice:
    """Варианты в порядке VARIANTS с рекомендацией и строками «лучше / хуже»; names — имена бригад по номеру.

    Базовые варианты сравниваются с рекомендованным, рекомендованный — со следующим по ключу рекомендации.
    Вариант «отдать бригаде» идёт последним, в рекомендации не участвует и всегда сравнивается с «Оптимально
    по дню»: диспетчер видит цену своего решения относительно оптимума дня.
    """
    request_id = choice_request_id(event)
    options = [
        VariantOption(
            variant=outcome.variant,
            title=variant_title(outcome.variant, event.type, names),
            summary=variant_summary(outcome.variant, event.type),
            metrics=outcome.plan.metrics,
            late=late_visits(outcome.plan),
            moved=len(outcome.diff.moved),
            request_engineer_id=_request_engineer(outcome.plan, request_id),
        )
        for outcome in sorted(outcomes, key=_order)
    ]
    ranked = sorted((option for option in options if option.variant in VARIANTS), key=_rank)
    best = ranked[0] if ranked else None
    optimal = next((option for option in options if option.variant == "optimal"), None)
    described = []
    for option in options:
        if option.variant in VARIANTS:
            reference = ranked[1] if option is best and len(ranked) > 1 else best
        else:
            reference = optimal
        compared = reference if reference is not None and reference is not option else None
        pros, cons = _compare(option, compared) if compared is not None else ([], [])
        described.append(
            option.model_copy(
                update={
                    "pros": pros,
                    "cons": cons,
                    "recommended": option is best,
                    "compared_to": compared.variant if compared is not None else None,
                }
            )
        )
    return EventChoice(
        entry_id=entry_id,
        event=event,
        metrics_before=before.metrics,
        late_before=late_visits(before),
        variants=described,
        current=current,
        assignable=is_assignable(event),
    )
