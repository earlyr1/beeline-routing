"""Варианты исправления плана на событие дня: стратегии, правило окна выбора, сравнение итогов и рекомендация.

Спецификации: docs/superpowers/specs/2026-09-17-event-variants-design.md (стратегии и сравнение) и
docs/superpowers/specs/2026-09-24-unified-events-design.md (единый поток событий). Классов событий нет: у любого
события считаются три стратегии, и окно выбора открывается по результату (needs_choice) — если «Ничего не менять»
ломает больше, чем лучший из пересчётов. Иначе событие молча применяется с «Ничего не менять», а сменить вариант
диспетчер может в любой момент.

У события об одной заявке, которая после события остаётся в плане (app/planning/facts.assignable), к трём
вариантам добавляется четвёртый — «отдать заявку названной бригаде» (стратегия «assign:<инженер>»). Его считают
по запросу диспетчера и сравнивают с «Оптимально по дню»: так видно, чего стоит решение отдать заявку не туда,
куда её кладёт оптимум.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.enums import SKILL_RU, TRANSPORT_RU, ReasonCode, request_label
from app.domain.models import Event, Plan, Request, Unassigned, Visit, dispatch_order
from app.planning.facts import subject_request_id
from app.planning.models import BaseVariant, EventChoice, EventVariant, PlanDiff, VariantOption
from app.solvers.assemble import build_plan
from app.solvers.problem import EngineerState, Problem
from app.solvers.simulate import simulate_route

VARIANTS: tuple[BaseVariant, ...] = ("optimal", "stable", "keep")
# Пересчёты, с которыми правило окна выбора сравнивает «Ничего не менять».
REPLANS: tuple[BaseVariant, ...] = ("optimal", "stable")
# «Минимум перестановок»: условные 500 км за перенос заявки к другому инженеру вместо 20. Снять заявку всё равно
# дороже (drop_normal), поэтому заявки пострадавшей бригады уходят другим, а чужие маршруты почти не трогаются.
STABLE_REASSIGNMENT = 500_000
# Сколько очередей распределения у заявок (app/domain/models.dispatch_order): по ним правило окна выбора взвешивает
# поломки важностью заявки.
_QUEUES = 4
VARIANT_TITLES: dict[EventVariant, str] = {
    "optimal": "Оптимально по дню",
    "stable": "Минимум перестановок",
    "keep": "Ничего не менять",
}
VARIANT_SUMMARIES: dict[EventVariant, str] = {
    "optimal": "Пересчитать остаток дня целиком",
    "stable": "Чужие маршруты почти не трогаем",
    "keep": "Только само событие, остальные маршруты как есть",
}
# Стратегия «отдать заявку бригаде»: «assign:E07». Бригада называется в заголовке варианта.
ASSIGN_PREFIX = "assign:"
ASSIGN_SUMMARY = "Выбор диспетчера"
KEEP_TITLE = VARIANT_TITLES["keep"]
KEEP_TEXT = f"Вариант «{KEEP_TITLE}»: план не пересчитан, заявку никто не забрал."
MAX_LINES = 3


def assign_variant(engineer_id: str) -> EventVariant:
    """Стратегия «отдать заявку бригаде engineer_id»."""
    return f"{ASSIGN_PREFIX}{engineer_id}"


def assigned_engineer(variant: EventVariant | None) -> str | None:
    """Бригада стратегии «отдать заявку», если это она; иначе None (в том числе у «assign:» без номера)."""
    if variant is None or not variant.startswith(ASSIGN_PREFIX):
        return None
    return variant[len(ASSIGN_PREFIX) :] or None


def variant_title(variant: EventVariant, names: Mapping[str, str] | None = None) -> str:
    """Название варианта для диспетчера; у «отдать бригаде» — имя бригады из names (иначе её номер)."""
    engineer_id = assigned_engineer(variant)
    if engineer_id is not None:
        return f"Отдать: {(names or {}).get(engineer_id, engineer_id)}"
    return VARIANT_TITLES[variant]


def variant_summary(variant: EventVariant) -> str:
    if assigned_engineer(variant) is not None:
        return ASSIGN_SUMMARY
    return VARIANT_SUMMARIES[variant]


def late_visits(plan: Plan) -> int:
    """Визиты плана, которые начнутся позже конца окна."""
    return sum(1 for route in plan.routes for visit in route.visits if visit.late_min > 0)


def breakages(plan: Plan) -> int:
    """Поломки плана для правила окна выбора: заявки без инженера и нарушения маршрутов.

    Нарушения плана (app/solvers/simulate.py) — визит позже окна (по одному на визит), работа после конца смены,
    обед, оборудование, навык, транспорт и плечо: то, что план обещает и не выполнит. Заявки, которые без инженера
    во всех вариантах одинаково, и опоздание уже начатой работы на сравнение вариантов не влияют.
    """
    return len(plan.unassigned) + plan.metrics.violations


def _by_queue(plan: Plan, queues: Mapping[str, int]) -> tuple[int, ...]:
    """Поломки плана по очередям распределения, от важной к остальным: сколько заявок каждой очереди осталось
    без инженера или ждёт опоздавшего инженера.

    queues — очередь заявки по номеру (dispatch_order): 0 — закреплённые диспетчером, 1 — аварии и срочные,
    2 — подключения, 3 — остальные. Заявка, которой там нет, считается в последней очереди.
    """
    counts = [0] * _QUEUES
    broken = [item.request_id for item in plan.unassigned]
    broken += [visit.request_id for route in plan.routes for visit in route.visits if visit.late_min > 0]
    for request_id in broken:
        counts[queues.get(request_id, _QUEUES - 1)] += 1
    return tuple(counts)


def needs_choice(plans: Mapping[BaseVariant, Plan], requests: Iterable[Request] = ()) -> bool:
    """Нужно ли окно выбора (правило Б): «Ничего не менять» ломает больше, чем лучший из пересчётов.

    plans — планы трёх базовых стратегий после события, requests — заявки дня после него. Поломки сравниваются
    двумя счетами: числом (breakages) и по важности заявок (_by_queue — сначала закреплённые, затем аварии и
    срочные, подключения, остальные). Окно нужно, если keep хуже лучшего пересчёта хоть по одному из них. Одного
    числа мало: в заполненном дне keep оставляет без инженера новую срочную заявку, а пересчёт ради неё снимает
    обычную — поломок поровну, но молча бросить срочную нельзя.

    Если выбор не нужен, событие применяется с «Ничего не менять»: пересчёт чужих маршрутов ничего бы не спас,
    а клиентам, которым уже назвали время, звонить незачем.
    """
    keep, replans = plans["keep"], [plans[variant] for variant in REPLANS]
    if breakages(keep) > min(breakages(plan) for plan in replans):
        return True
    queues = {request.id: dispatch_order(request) for request in requests}
    return _by_queue(keep, queues) > min(_by_queue(plan, queues) for plan in replans)


def keep_plan(problem: Problem, unassigned_before: Collection[str] = ()) -> Plan:
    """«Ничего не менять»: прежние маршруты без решателя на задаче после события.

    Порядок заявок инженера — его несделанная часть плана до события (Problem.previous_order из pin_problem).
    Маршруты прогоняются обычной симуляцией: опоздания и переработки видны в визитах и нарушениях. Заявки
    инженера, которому больше нельзя работать (недоступен или задержан до конца смены), и заявки, которые инженер
    больше не может взять (нужен навык или транспорт, которого у него нет), остаются без инженера. Новая срочная
    заявка ни в чей маршрут не попадает. unassigned_before — заявки без инженера в плане до события: их причину
    считает build_plan, как обычно, а не пишет этот вариант.

    Времена визитов тоже остаются прежними (Problem.previous_start): маршрут, из которого заявка ушла, не
    сжимается, бригада получает окно, а следующим клиентам не приходится звонить, что инженер приедет раньше.
    Опоздать визит по-прежнему может: задержку и объезд времена держать не мешают.

    Единственное, что вариант делает сам, — общее правило закреплённых заявок: заявка, закреплённая за бригадой
    (Request.fixed_engineer_id — переназначение диспетчера или «отдать бригаде»), которой нет в маршруте этой
    бригады, встаёт в него (_insert). Бригада пропускает то, на что со вставкой не успевает; её маршрут меняется
    нарочно, и времена в нём считаются заново, а остальные маршруты держат прежние.
    """
    sequences, fixed = _previous_routes(problem)
    before = set(unassigned_before)
    rerouted: set[str] = set()
    for request_id in problem.open_request_ids:
        engineer_id = problem.request(request_id).fixed_engineer_id
        if engineer_id is None or request_id in sequences.get(engineer_id, []):
            continue
        for sequence in sequences.values():
            if request_id in sequence:
                sequence.remove(request_id)
        fixed.pop(request_id, None)
        if _insert(problem, sequences, fixed, request_id, engineer_id):
            rerouted.add(engineer_id)
        else:
            # Заявку не вставить никуда: её причину считает build_plan.
            before.add(request_id)
    changed = {request_id for engineer_id in rerouted for request_id in sequences[engineer_id]}
    held = {rid: start for rid, start in problem.previous_start.items() if rid not in changed}
    return _routes_plan(problem, sequences, fixed, before, held)


def _insert(
    problem: Problem,
    sequences: dict[str, list[str]],
    fixed: dict[str, Unassigned],
    request_id: str,
    engineer_id: str,
) -> bool:
    """Вставляет закреплённую заявку request_id в маршрут бригады engineer_id; False — не вставить никуда.

    Для каждого места вставки маршрут идёт по порядку «заявки до места, новая заявка, заявки после места». Бригада
    берёт новую заявку и добавляет к ней свои по очереди распределения (dispatch_order): сначала закреплённые
    диспетчером, затем аварии и срочные, затем подключения, затем ремонт и дозаказ, в каждой группе — по прежнему
    порядку. Заявка берётся, если маршрут со всеми взятыми остаётся не хуже прежнего (_no_worse), иначе бригада её
    пропускает. Из мест берётся то, где пропущено меньше закреплённых заявок, затем аварий, затем подключений,
    затем всех, затем короче маршрут; при равенстве — место раньше. Пропущенные заявки остаются без инженера
    с причиной в fixed, sequences получает новый маршрут бригады.
    """
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
    if best is None:
        return False
    _, kept, skipped = best
    sequences[engineer_id] = kept
    label = request_label(request_id, problem.request(request_id).priority)
    text = f"Вариант «{KEEP_TITLE}»: {state.engineer.name} пропускает заявку, чтобы успеть к заявке {label}."
    # Заявку, на которую у бригады уже не осталось оборудования, она пропускает не из-за времени: взять
    # единицу днём негде, и «чтобы успеть» про неё было бы неправдой.
    no_equipment = state.equipment_left - problem.equipment_used(kept) <= 0
    short = (
        f"Вариант «{KEEP_TITLE}»: у {state.engineer.name} не осталось оборудования на эту заявку: "
        f"утром бригада взяла {state.engineer.equipment_stock} ед."
    )
    for rid in skipped:
        reason = short if no_equipment and problem.request(rid).needs_equipment else text
        fixed[rid] = Unassigned(request_id=rid, reason_code=ReasonCode.NO_FREE_ENGINEER, reason_text=reason)
    return True


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


def _previous_routes(problem: Problem) -> tuple[dict[str, list[str]], dict[str, Unassigned]]:
    """Прежние маршруты на задаче после события и заявки, которые из них выпадают, с причиной «Ничего не менять».

    Выпадают заявки инженера, которому больше нельзя работать, и заявки, которые он больше не может взять: нужен
    навык или транспорт, которого у него нет (сменился транспорт инженера или изменили заявку).
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
                    request_id=request_id, reason_code=ReasonCode.NO_FREE_ENGINEER, reason_text=KEEP_TEXT
                )
            elif request.skill not in engineer.skills:
                fixed[request_id] = Unassigned(
                    request_id=request_id,
                    reason_code=ReasonCode.NO_SKILL,
                    reason_text=f"{KEEP_TEXT} У {engineer.name} нет навыка «{SKILL_RU[request.skill]}».",
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
    return sequences, fixed


def _routes_plan(
    problem: Problem,
    sequences: dict[str, list[str]],
    fixed: dict[str, Unassigned],
    unassigned_before: Collection[str],
    not_before: Mapping[str, int],
) -> Plan:
    """План «Ничего не менять». Открытые заявки вне маршрутов получают причину варианта, кроме бывших без инженера.

    not_before — времена визитов, которые вариант держит: прежние, кроме маршрутов, куда встала закреплённая заявка.
    """
    before = set(unassigned_before)
    placed = {request_id for sequence in sequences.values() for request_id in sequence}
    for request_id in problem.open_request_ids:
        if request_id not in placed and request_id not in fixed and request_id not in before:
            fixed[request_id] = Unassigned(
                request_id=request_id, reason_code=ReasonCode.NO_FREE_ENGINEER, reason_text=KEEP_TEXT
            )
    return build_plan(problem, "ortools", sequences, fixed_unassigned=fixed, not_before=not_before)


@dataclass(frozen=True)
class Outcome:
    """Итог одной стратегии: план после события и разница с планом до него."""

    variant: EventVariant
    plan: Plan
    diff: PlanDiff


def _clients(option: VariantOption) -> int:
    return option.metrics.unassigned + option.late


def _numbers(option: VariantOption) -> tuple[int, int, int, float]:
    """Итоги варианта, по которым считаются «лучше / хуже»: с одинаковыми числами сравнивать варианты нечем."""
    return (
        _clients(option),
        option.metrics.engineers_used,
        option.moved,
        round(option.metrics.total_km, 1),
    )


def _rank(option: VariantOption) -> tuple[int, int, int, float, int]:
    return (*_numbers(option), VARIANTS.index(option.variant))


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


def _differing(best: VariantOption, ranked: Sequence[VariantOption]) -> VariantOption | None:
    """Ближайший к рекомендованному вариант с другими числами; None — все варианты одинаковые."""
    return next((option for option in ranked if _numbers(option) != _numbers(best)), None)


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
    *,
    assignable: bool = False,
) -> EventChoice:
    """Варианты в порядке VARIANTS с рекомендацией и строками «лучше / хуже»; names — имена бригад по номеру.

    event — событие, как его сохранил план (с полями, которые заполняет backend: окно называет, что изменилось).
    assignable — заявку события можно отдать выбранной бригаде (app/planning/facts.assignable по заявкам после
    события).

    Базовые варианты сравниваются с рекомендованным, рекомендованный — со следующим по ключу рекомендации.
    Если у него те же числа, рекомендованный сравнивается с ближайшим вариантом, у которого они другие: сравнение
    с таким же вариантом не объясняет ничего. compared_to называет этот вариант, чтобы его назвал и экран; у
    рекомендованного он пустой, только когда все варианты дают одни и те же числа — тогда событие ничего не меняет,
    какой вариант ни возьми.
    Вариант «отдать бригаде» идёт последним, в рекомендации не участвует и всегда сравнивается с «Оптимально
    по дню»: диспетчер видит цену своего решения относительно оптимума дня.
    """
    request_id = subject_request_id(event)
    options = [
        VariantOption(
            variant=outcome.variant,
            title=variant_title(outcome.variant, names),
            summary=variant_summary(outcome.variant),
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
            reference = _differing(option, ranked) if option is best else best
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
        assignable=assignable,
    )
