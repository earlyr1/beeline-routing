"""Состояние планирования одного датасета и применение событий дня.

Событие любого типа применяется одним путём (apply_event): что изменилось в фактах дня, решает слой фактов
(app/planning/facts.py) — единственное место, где важен тип события, — а здесь закрепляется сделанное и считается
стратегия. Спецификация: docs/superpowers/specs/2026-09-24-unified-events-design.md.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.domain.enums import RequestStatus, RequestTier
from app.domain.models import Engineer, Event, Lunch, Office, Plan, Request, Visit
from app.domain.timeutil import fmt_hhmm
from app.domain.windows import TimeSlot
from app.geo.kvcache import KVCache
from app.geo.matrix import TrafficProfile, TravelModel, TravelTimes
from app.geo.osrm import OsrmClient
from app.geo.transit import TransitLookup, TransitMatrix
from app.ingest.geocode import GeoResult
from app.planning.delay import keep_delays
from app.planning.diff import compute_diff
from app.planning.facts import EventRejected, Facts, assignable, delayed_until, event_facts
from app.planning.models import AppliedEvent, EventVariant, PlanDiff, PrecomputedPlan
from app.planning.night import NightChoice, choose_night_plan, plan_routes
from app.planning.variants import STABLE_REASSIGNMENT, assigned_engineer, keep_plan
from app.planning.workload import DEFAULT_WORKLOAD_LEVEL, travel_buffer, workload_weights
from app.settings import DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S, DEFAULT_SOLVER_TIME_LIMIT_S
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import ObjectiveWeights, OrToolsSolver
from app.solvers.portfolio import SolverPool, plan_cost
from app.solvers.problem import EngineerState, Problem, make_problem, problem_points

logger = logging.getLogger(__name__)


@dataclass
class PlanningContext:
    model: TravelModel
    traffic: TrafficProfile
    osrm: OsrmClient | None = None
    cache: KVCache | None = None
    # Матрицы времени на общественном транспорте от 2ГИС по регионам, если диспетчер посчитал их и положил
    # файлы рядом. make_problem берёт из них минуты по парам точек, привязывая точку дня к ближайшей точке матрицы
    # в 150 м: после срочной заявки пары прежних точек остаются из 2ГИС, а пары с новой точкой считает модель.
    transit: Sequence[TransitMatrix] = ()
    # Пул процессов для поисков OR-Tools (app/solvers/portfolio.py); None — поиск в текущем процессе.
    solver_pool: SolverPool | None = None
    # Лимит OR-Tools на день без обеда и на перепланирование по событию.
    time_limit_s: int = DEFAULT_SOLVER_TIME_LIMIT_S
    # Лимит OR-Tools на весь день с обедом.
    time_limit_lunch_s: int = DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S
    geocode: Callable[[str, str], GeoResult] | None = None
    # Уровень распределения по типу заявки BK (tier_by_bk в config/synth_config.yaml), тот же, что у заявок бандла:
    # срочная заявка диспетчера получает уровень своего типа работ. Тип, которого здесь нет, — авария.
    tier_by_bk: Mapping[str, RequestTier] = field(default_factory=dict)
    # Сетка окон визита (SynthConfig.window_grid): по ней помощник кладёт на слот окно, которое он назвал, — окно
    # клиенту называют слотом. Пустая сетка — её никто не задал (планировщик в тестах, скрипты), и окно идёт как есть.
    window_grid: Sequence[TimeSlot] = ()
    # Каталог ночных планов (app/planning/night.py): <каталог>/<регион>/night_plan*.json, файл на пару (уровень
    # нагрузки, обед); в сервисе это data/bundles. None — ночные планы не ищутся, утренний план всегда ищется при
    # загрузке.
    night_plan_dir: Path | None = None

    def day_time_limit_s(self, lunch_enabled: bool) -> int:
        """Лимит на план всего дня с нуля: с обедом поиск дольше, без обеда как у перепланирования."""
        return self.time_limit_lunch_s if lunch_enabled else self.time_limit_s


@dataclass(frozen=True)
class PlanningSession:
    dataset_id: str
    region: str
    office: Office
    requests: list[Request]
    engineers: list[Engineer]
    control: Plan | None
    problem: Problem
    plan: Plan
    baseline: Plan
    previous_plan: Plan | None = None
    last_diff: PlanDiff | None = None
    events: list[AppliedEvent] = field(default_factory=list)
    now: int = 0
    version: int = 1
    # Уровень нагрузки дня (app/planning/workload.py): веса OR-Tools и запас на дорогу во всех решениях сессии.
    workload_level: int = DEFAULT_WORKLOAD_LEVEL
    # Обед по плану, выбранный для дня: действует во всех решениях сессии, включая FCFS и перепланирование.
    lunch_enabled: bool = True
    # Утренний план взят из ночного расчёта; None — найден при загрузке дня. События дня его не меняют: это
    # происхождение утреннего плана, от которого пересчитываются события.
    precomputed: PrecomputedPlan | None = None

    def request(self, request_id: str) -> Request | None:
        return next((r for r in self.requests if r.id == request_id), None)

    def engineer(self, engineer_id: str) -> Engineer | None:
        return next((e for e in self.engineers if e.id == engineer_id), None)


def day_problem(
    requests: list[Request],
    engineers: list[Engineer],
    ctx: PlanningContext,
    workload_level: int,
    lunch_enabled: bool,
    travel: TravelTimes | None = None,
) -> Problem:
    """Задача на начало дня с запасом на дорогу уровня нагрузки и выбранным обедом.

    Базовая матрица берётся из кэша, если уже была; travel — готовая матрица тех же точек (подъём дня из базы).
    """
    return make_problem(
        requests,
        engineers,
        model=ctx.model,
        traffic=ctx.traffic,
        osrm=ctx.osrm,
        cache=ctx.cache,
        buffer=travel_buffer(workload_level),
        lunch=lunch_enabled,
        transit=ctx.transit,
        travel=travel,
    )


def search_plan(
    problem: Problem,
    weights: ObjectiveWeights,
    time_limit_s: int,
    pool: SolverPool | None = None,
    *,
    share: int = 1,
    seed: Plan | None = None,
) -> Plan:
    """Поиск OR-Tools: с пулом процессов сразу несколькими стратегиями, без пула — одной в текущем процессе.

    share — сколько поисков делят пул одновременно. seed — допустимый план этой же задачи (ночной план, который
    не подошёл утренним): первая стратегия стартует от его маршрутов, остальные строят первое решение сами, и
    результат не хуже seed по цели при весах weights. Этой же функцией ищет ночной план scripts/night_plan.py.
    """
    start = plan_routes(seed) if seed is not None else None
    if pool is not None:
        plan = pool.solve(problem, weights, time_limit_s, pool.strategies(share), start=start)
    else:
        # Подсказку OR-Tools берёт из previous_order: копия задачи только для поиска, в сессию она не попадает.
        hinted = replace(problem, previous_order=start) if start else problem
        plan = OrToolsSolver(time_limit_s=time_limit_s, weights=weights).solve(hinted)
    if seed is None:
        return plan
    # Страховка на случай, если поиск от подсказки ушёл в сторону или подсказку не принял: min берёт первый из
    # равных, то есть найденный план.
    return min((plan, seed), key=lambda item: plan_cost(problem, item, weights))


def _solve(
    problem: Problem,
    workload_level: int,
    time_limit_s: int,
    variant: EventVariant = "optimal",
    pool: SolverPool | None = None,
    share: int = 1,
    seed: Plan | None = None,
) -> tuple[Plan, Plan]:
    """Оптимизированный план с весами уровня нагрузки и базовый FCFS. Стоимость инженера FCFS не использует.

    variant="stable" делает перенос заявки к другому инженеру очень дорогим (вариант «Минимум перестановок»).
    Остальные стратегии, в том числе «отдать заявку бригаде», ищут с обычными весами уровня нагрузки: заявку
    к бригаде привязывает закрепление в самой заявке, а не веса.
    С пулом процессов поиск идёт сразу несколькими стратегиями и берётся лучший план; share — сколько поисков
    делят пул одновременно (варианты события считаются вместе). seed — план, от которого стартует поиск
    (search_plan).
    """
    weights = workload_weights(workload_level)
    if variant == "stable":
        weights = replace(weights, reassignment=STABLE_REASSIGNMENT)
    plan = search_plan(problem, weights, time_limit_s, pool, share=share, seed=seed)
    return plan, FcfsSolver().solve(problem)


def warn_stale_transit(region: str, problem: Problem, transit: Sequence[TransitMatrix]) -> None:
    """Пишет в лог, если у региона дня есть матрица 2ГИС, а часть точек дня к ней не привязалась.

    На известном файле региона привязываются все точки. Если нет, в дне новые адреса или матрица посчитана для
    другой сборки бандла: такие пары молча уходят на формулу, поэтому диспетчеру стоит об этом знать.
    """
    own = [matrix for matrix in transit if matrix.region == region]
    if not own:
        return
    points = problem_points(problem.engineers, problem.requests)
    lookup = TransitLookup(points, own)
    if lookup.snapped < len(points):
        logger.warning(
            "2ГИС: к матрице региона %s привязалось %d из %d точек дня. Новые адреса или матрица устарела "
            "(пересчитайте: python -m scripts.transit_matrix --region %s --force); пары остальных точек "
            "считает формула.",
            region,
            lookup.snapped,
            len(points),
            region,
        )


def start_session(
    dataset_id: str,
    region: str,
    office: Office,
    requests: list[Request],
    engineers: list[Engineer],
    control: Plan | None,
    ctx: PlanningContext,
    *,
    workload_level: int = DEFAULT_WORKLOAD_LEVEL,
    lunch_enabled: bool = True,
) -> PlanningSession:
    """План всего дня с нуля: предподсчёт загрузки и пересборка дня. Лимит OR-Tools зависит от обеда.

    Сначала ищется ночной план региона для уровня нагрузки и обеда дня (app/planning/night.py): если отпечаток
    задачи совпал и маршруты проходят проверку, он и есть утренний план, и поиска нет. Иначе план ищется, как
    всегда; если маршруты ночного плана на этой задаче допустимы, поиск стартует от них. FCFS считается в любом
    случае.
    """
    problem = day_problem(requests, engineers, ctx, workload_level, lunch_enabled)
    warn_stale_transit(region, problem, ctx.transit)
    try:
        night = choose_night_plan(ctx.night_plan_dir, region, problem, workload_level, lunch_enabled)
    except Exception:  # noqa: BLE001 - ночной план только ускоряет утро и не должен мешать загрузке дня
        logger.exception("Ночной план региона %r не проверен из-за ошибки: план ищется при загрузке", region)
        night = NightChoice()
    if night.plan is not None:
        plan, baseline = night.plan, FcfsSolver().solve(problem)
    else:
        plan, baseline = _solve(
            problem,
            workload_level,
            ctx.day_time_limit_s(lunch_enabled),
            pool=ctx.solver_pool,
            seed=night.seed,
        )
    return PlanningSession(
        dataset_id=dataset_id,
        region=region,
        office=office,
        requests=list(requests),
        engineers=list(engineers),
        control=control,
        problem=problem,
        plan=plan,
        baseline=baseline,
        workload_level=workload_level,
        lunch_enabled=lunch_enabled,
        precomputed=night.precomputed,
    )


def _on_the_way(problem: Problem, state: EngineerState, visit: Visit, now: int, open_ids: set[str]) -> bool:
    """Инженер выехал к визиту раньше now, и выезд в now задержал бы начало работы.

    Отменённая заявка и недоступный инженер не закрепляются: визит возвращается в пул.
    """
    if visit.request_id not in open_ids or not state.engineer.available or state.available_until <= now:
        return False
    if visit.arrival - visit.leg_min >= now:
        return False
    window_start = problem.request(visit.request_id).window_start
    return max(now + visit.leg_min, window_start) > visit.start


def pin_problem(problem: Problem, plan: Plan, now: int, released: Collection[str] = ()) -> Problem:
    """Закрепляет визиты, начатые до now, и визит, к которому инженер уже едет.

    Инженер продолжает день из точки последнего закреплённого визита в его время окончания.
    Visit.pinned остаётся True только у начатой работы: для диспетчера «закреплена» значит «уже
    в работе, отменить нельзя». Визит в пути солвер не трогает, но отменить его можно до начала работы.
    Визит в пути к заявке из released (её только что изменили) не удерживается: солвер решает заново.
    Обед, начатый до now, остаётся как в прежнем плане (в том числе у инженера, который стал недоступен), и новый
    обед инженеру уже не нужен. Инженер на обеде свободен не раньше конца обеда.
    Оборудование, выданное на закреплённых визитах, назад не возвращается: на остаток дня у бригады остаётся
    утренний запас минус выданные единицы, и новых в офисе она не берёт.
    Времена несделанных визитов запоминаются в previous_start: это то, что обещано клиентам, и «Ничего
    не менять» их держит.
    """
    routes = {route.engineer_id: route for route in plan.routes}
    open_ids = set(problem.open_request_ids)
    pinned: dict[str, list[Visit]] = {}
    pinned_lunch: dict[str, Lunch] = {}
    previous_assignment: dict[str, str] = {}
    previous_order: dict[str, list[str]] = {}
    previous_start: dict[str, int] = {}
    pinned_ids: set[str] = set()
    states: list[EngineerState] = []
    for state in problem.states:
        engineer_id = state.engineer.id
        route = routes.get(engineer_id)
        visits = route.visits if route else []
        lunch = route.lunch if route and route.lunch and route.lunch.start < now else None
        done = [visit for visit in visits if visit.start < now]
        upcoming = [visit for visit in visits if visit.start >= now]
        if (
            upcoming
            and upcoming[0].request_id not in released
            and _on_the_way(problem, state, upcoming[0], now, open_ids)
        ):
            done.append(upcoming.pop(0))
        rest = [visit.request_id for visit in upcoming]
        start_node, available_from = state.start_node, max(state.available_from, now)
        if done:
            start_node = problem.request_node(done[-1].request_id)
            available_from = max(available_from, done[-1].end)
        if lunch is not None:
            pinned_lunch[engineer_id] = lunch
            available_from = max(available_from, lunch.end)
        pinned[engineer_id] = [visit.model_copy(update={"pinned": visit.start < now}) for visit in done]
        pinned_ids.update(visit.request_id for visit in done)
        previous_order[engineer_id] = rest
        previous_assignment.update({request_id: engineer_id for request_id in rest})
        previous_start.update({visit.request_id: visit.start for visit in upcoming})
        spent = problem.equipment_used(visit.request_id for visit in done)
        states.append(
            EngineerState(
                state.engineer,
                start_node,
                available_from,
                state.available_until,
                state.equipment_left - spent,
            )
        )
    return replace(
        problem,
        states=states,
        open_request_ids=[rid for rid in problem.open_request_ids if rid not in pinned_ids],
        pinned=pinned,
        pinned_lunch=pinned_lunch,
        previous_assignment=previous_assignment,
        previous_order=previous_order,
        previous_start=previous_start,
        now=now,
    )


def _pin_request(requests: list[Request], request_id: str | None, engineer_id: str) -> None:
    """Закрепляет заявку за бригадой: решатель не отдаст её другим (Exclusion.FIXED_TO_OTHER)."""
    for request in requests:
        if request.id == request_id:
            request.fixed_engineer_id = engineer_id


def _release_pins(requests: list[Request], engineers: list[Engineer]) -> list[Request]:
    """Снимает закрепление заявок за бригадой, которая больше не может их взять: заявка возвращается в общий пул.

    Бригада не может взять заявку, если её нет в дне, она недоступна, у неё нет навыка заявки или нет транспорта,
    который нужен заявке. Время на бригаде здесь не проверяется: не успевает — заявка остаётся без инженера.
    """
    by_id = {engineer.id: engineer for engineer in engineers}
    released: list[Request] = []
    for request in requests:
        engineer = by_id.get(request.fixed_engineer_id or "")
        if request.fixed_engineer_id is not None and (
            engineer is None
            or not engineer.available
            or request.skill not in engineer.skills
            or request.transport_required not in (None, engineer.transport)
        ):
            request = request.model_copy(update={"fixed_engineer_id": None})
        released.append(request)
    return released


def _pinned_problem(base: Problem, session: PlanningSession, facts: Facts) -> Problem:
    """Задача на остаток дня после события: закреплённая работа, прежние задержки и поправка самого события.

    Визит в пути к заявкам facts.released не удерживается. Поправка события (facts.adjust) может закрепить
    сделанное заново, отпустив ещё какие-то заявки: так задержка отпускает заявку, в окно которой инженер
    с задержкой уже не успеет.
    """
    until = delayed_until(applied.event for applied in session.events)

    def pin(released: Collection[str] = ()) -> Problem:
        return keep_delays(
            pin_problem(base, session.plan, facts.event.time, {*facts.released, *released}), until
        )

    problem = pin()
    return facts.adjust(problem, pin) if facts.adjust is not None else problem


def early_event_text(time: int, now: int) -> str:
    return f"Время события {fmt_hhmm(time)} раньше текущего времени плана {fmt_hhmm(now)}."


def _check_time(session: PlanningSession, event: Event) -> None:
    if event.time < session.now:
        raise EventRejected(early_event_text(event.time, session.now))


def check_event(session: PlanningSession, event: Event, ctx: PlanningContext) -> Event:
    """Проверяет событие против текущего состояния без пересчёта плана.

    Возвращает событие в том виде, в каком apply_event его сохранит (у срочной заявки появляются
    координаты из геокодера). Бросает EventRejected с текстом для диспетчера. Сессию не меняет.
    """
    _check_time(session, event)
    return event_facts(session, event, ctx).event


def apply_event(
    session: PlanningSession,
    event: Event,
    ctx: PlanningContext,
    *,
    version: int | None = None,
    variant: EventVariant = "optimal",
) -> PlanningSession:
    """Применяет одно событие дня и возвращает НОВУЮ сессию; входная не меняется.

    Путь один для всех типов событий: факты дня после события (app/planning/facts.py) → снять закрепления,
    которые бригада больше не держит → «отдать бригаде», если это стратегия → задача на остаток дня с закреплённой
    работой и поправкой события → «Ничего не менять» или решатель → разница планов с подписью события.

    Бросает EventRejected, если событие противоречит текущему состоянию. Уровень нагрузки и обед остаются как в
    сессии. Лимит OR-Tools обычный и с обедом: перепланирование стартует от текущего плана. version — номер нового
    плана (у сессии и у применённого события); без него следующий за номером входной сессии.

    variant — стратегия (app/planning/variants.py): «optimal», «stable», «keep» или «assign:<инженер>». «Отдать
    бригаде» действует, если событие это допускает (facts.assignable: оно об одной заявке, бригаду ей не называет,
    и после события заявка в работе); иначе это «Оптимально по дню».
    """
    _check_time(session, event)
    facts = event_facts(session, event, ctx)
    engineers = facts.engineers
    requests = _release_pins(facts.requests, engineers)
    chosen = assigned_engineer(variant)
    if chosen is not None and assignable(facts.event, requests):
        # «Отдать заявку бригаде»: закрепляем её до сборки задачи, как это делает переназначение, и дальше
        # считаем обычным «Оптимально по дню». Событие не отклоняется ни при какой бригаде: цену решения
        # показывает план — не успевающая бригада оставит заявку или свою соседнюю без инженера. Бригаде,
        # которая заявку взять не может (нет навыка или транспорта, недоступна), закрепление снимают те же
        # правила _release_pins и сразу, а не на следующем событии: диспетчер утверждает тот план, который
        # останется. У остальных закрепление держит заявку у бригады и в следующих событиях.
        _pin_request(requests, facts.subject_request_id, chosen)
        requests = _release_pins(requests, engineers)
    base = day_problem(requests, engineers, ctx, session.workload_level, session.lunch_enabled)
    problem = _pinned_problem(base, session, facts)
    if facts.check is not None:
        facts.check(problem)
    if variant == "keep":
        plan = keep_plan(problem, {item.request_id for item in session.plan.unassigned})
        baseline = FcfsSolver().solve(problem)
    else:
        # Пул делят «Оптимально» и «Минимум перестановок»: без выбора диспетчера у события считаются обе, и
        # одновременно. «Отдать бригаде» считается одна, по запросу диспетчера, но получает такую же долю пула:
        # её цену диспетчер сравнивает с «Оптимально по дню», а более широкий поиск нашёл бы план не хуже и
        # занизил её. Та же доля и у стратегии, выбранной сразу (/events, помощник): план не зависит от того,
        # как событие попало на шкалу.
        plan, baseline = _solve(
            problem, session.workload_level, ctx.time_limit_s, variant, pool=ctx.solver_pool, share=2
        )
    cancelled = {request.id for request in requests if request.status == RequestStatus.CANCELLED}
    diff = compute_diff(session.plan, plan, cancelled)
    if facts.annotate is not None:
        diff = facts.annotate(diff, problem, session.plan)
    version = session.version + 1 if version is None else version
    applied = AppliedEvent(id=f"ev_{len(session.events) + 1}", event=facts.event, version=version)
    return replace(
        session,
        requests=requests,
        engineers=engineers,
        problem=problem,
        plan=plan,
        baseline=baseline,
        previous_plan=session.plan,
        last_diff=diff,
        events=[*session.events, applied],
        now=event.time,
        version=version,
    )
