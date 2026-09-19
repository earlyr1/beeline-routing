"""Варианты исправления плана на «ломающее» событие: стратегии, сравнение и тексты. Без шкалы и API."""

from dataclasses import replace

import pytest

from app.domain.enums import EventType, ReasonCode, Skill, Transport
from app.domain.models import Event, Metrics, Plan, Route, Visit
from app.planning import session as session_module
from app.planning.models import DiffMove, PlanDiff
from app.planning.session import apply_event
from app.planning.variants import (
    KEEP_TEXT,
    STABLE_REASSIGNMENT,
    VARIANTS,
    Outcome,
    assign_variant,
    build_choice,
    insert_plan,
    is_choosable,
    keep_plan,
    late_visits,
    variant_summary,
    variant_title,
)
from tests.helpers import eng, problem_of, req
from tests.planning_helpers import busy_engineer, context, new_session, routes
from tests.timeline_helpers import fcfs_solves


@pytest.fixture
def solves(monkeypatch):
    return fcfs_solves(monkeypatch)


def _upcoming(plan, engineer_id, minute):
    route = next(route for route in plan.routes if route.engineer_id == engineer_id)
    return [visit.request_id for visit in route.visits if visit.start >= minute]


def test_only_events_that_break_the_plan_are_choosable():
    choosable = {
        EventType.URGENT,
        EventType.ENGINEER_UNAVAILABLE,
        EventType.ENGINEER_TRANSPORT_CHANGED,
        EventType.ENGINEER_DELAYED,
        EventType.REQUEST_REASSIGNED,
    }
    for event_type in EventType:
        event = Event.model_construct(type=event_type, time=780)
        assert is_choosable(event) is (event_type in choosable)
    assert VARIANTS == ("optimal", "stable", "keep")


def test_keep_leaves_the_upcoming_visits_of_an_unavailable_engineer_without_an_engineer(solves):
    ctx = context()
    base = new_session(ctx)
    busy = busy_engineer(base.plan)
    upcoming = _upcoming(base.plan, busy, 13 * 60)
    assert upcoming, "в тестовом дне у занятого инженера должны быть визиты после 13:00"
    event = Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id=busy)
    solves.clear()

    kept = apply_event(base, event, ctx, variant="keep")

    before = routes(base.plan)
    after = routes(kept.plan)
    assert after[busy] == [rid for rid in before[busy] if rid not in upcoming]
    assert all(after[other] == before[other] for other in before if other != busy)
    reasons = {item.request_id: item for item in kept.plan.unassigned}
    assert set(upcoming) <= set(reasons)
    assert all(reasons[rid].reason_text == KEEP_TEXT for rid in upcoming)
    # Без решателя: FCFS в тестах подменяет _solve, и «ничего не менять» его не вызывает.
    assert solves == []


def test_keep_leaves_the_own_reason_of_a_request_that_was_unassigned_before_the_event(solves):
    ctx = context()
    requests = [
        req("R1", 1, 0, "10:00", "12:00"),
        req("R2", 1.2, 0, "14:00", "16:00"),
        req("RX", -1, 0, "15:00", "17:00", skill=Skill.EMERGENCY),
    ]
    engineers = [eng("E1", skills=[Skill.LOCAL]), eng("E2", skills=[Skill.LOCAL])]
    base = new_session(ctx, requests=requests, engineers=engineers)
    busy = busy_engineer(base.plan)
    upcoming = _upcoming(base.plan, busy, 13 * 60)
    before = {item.request_id: item for item in base.plan.unassigned}
    assert upcoming and before["RX"].reason_code == ReasonCode.NO_SKILL
    event = Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id=busy)

    kept = apply_event(base, event, ctx, variant="keep")

    reasons = {item.request_id: item for item in kept.plan.unassigned}
    # Без инженера заявка была и до события: у неё своя причина, а не «план не пересчитан».
    assert reasons["RX"] == before["RX"]
    assert all(reasons[rid].reason_text == KEEP_TEXT for rid in upcoming)


def test_keep_drops_visits_that_need_a_transport_the_engineer_no_longer_has(solves):
    ctx = context()
    requests = [
        req("R1", 1, 0, "10:00", "12:00"),
        req("R2", 1.2, 0, "14:00", "16:00", transport=Transport.CAR),
        req("R3", -1, 0, "15:00", "17:00"),
    ]
    base = new_session(ctx, requests=requests, engineers=[eng("E1"), eng("E2")])
    owner = next(
        route.engineer_id for route in base.plan.routes if "R2" in [v.request_id for v in route.visits]
    )
    event = Event(
        type=EventType.ENGINEER_TRANSPORT_CHANGED, time="13:00", engineer_id=owner, transport=Transport.BIKE
    )

    kept = apply_event(base, event, ctx, variant="keep")

    reasons = {item.request_id: item for item in kept.plan.unassigned}
    assert reasons["R2"].reason_code == ReasonCode.NO_TRANSPORT
    assert reasons["R2"].reason_text.startswith(KEEP_TEXT)
    assert "R2" not in routes(kept.plan)[owner]


def test_keep_shifts_a_delayed_route_and_shows_the_lateness(solves):
    ctx = context()
    base = new_session(ctx)
    busy = busy_engineer(base.plan)
    # На объекте R1 до 16:30: R2 и R3 начнутся позже своих окон.
    event = Event(type=EventType.ENGINEER_DELAYED, time="10:05", engineer_id=busy, delay_min=360)
    solves.clear()

    kept = apply_event(base, event, ctx, variant="keep")

    assert late_visits(kept.plan) > 0
    kept_route = routes(kept.plan)[busy]
    assert kept_route == [rid for rid in routes(base.plan)[busy] if rid in kept_route]
    assert solves == []


def test_keep_leaves_an_urgent_request_without_an_engineer(solves):
    ctx = context()
    base = new_session(ctx)
    urgent = req("U1", 0.5, 0.5, "13:00", "15:00")
    event = Event(type=EventType.URGENT, time="12:00", request=urgent)

    kept = apply_event(base, event, ctx, variant="keep")

    assert {item.request_id: item.reason_text for item in kept.plan.unassigned}["U1"] == KEEP_TEXT
    assert all("U1" not in visits for visits in routes(kept.plan).values())


def test_stable_solves_with_an_expensive_reassignment_and_optimal_keeps_the_level_weights(monkeypatch):
    captured = []

    class Capture:
        def __init__(self, time_limit_s, weights):
            captured.append(weights)

        def solve(self, problem):
            return Plan(
                solver="ortools",
                routes=[],
                unassigned=[],
                metrics=Metrics(engineers_used=0, km_per_engineer={}, total_km=0, assigned=0, unassigned=0),
            )

    monkeypatch.setattr(session_module, "OrToolsSolver", Capture)
    problem = new_session(context()).problem
    captured.clear()
    session_module._solve(problem, 1, 1)
    session_module._solve(problem, 1, 1, "stable")

    assert captured[0].reassignment == 20_000
    assert captured[1].reassignment == STABLE_REASSIGNMENT == 500_000
    assert captured[1].vehicle_fixed_cost == captured[0].vehicle_fixed_cost


def test_stable_moves_fewer_requests_to_other_brigades_than_optimal_with_or_tools():
    """Настоящий OR-Tools на собранном дне.

    Утром инженеры далеко друг от друга: M1 у офиса, M2 в 30 км. Срочная U у офиса приходит на то же время, что B
    инженера с M1, обе одному не успеть. «Оптимально» отдаёт U ему, а B переносит второму: так на 31 км короче, это
    больше штрафа 20 км за перенос. «Минимум перестановок» везёт U второму инженеру: перенос стоит 500 км.
    """
    ctx = context()
    requests = [
        req("M1", 0, 0, "09:00", "09:30", duration=120),
        req("M2", 30, 0, "09:00", "11:00", duration=120),
        req("B", 12, 0, "15:00", "15:20", duration=60),
    ]
    base = new_session(ctx, requests=requests, engineers=[eng("E1"), eng("E2")], lunch_enabled=False)
    near = next(engineer_id for engineer_id, visits in routes(base.plan).items() if "M1" in visits)
    assert routes(base.plan)[near] == ["M1", "B"]
    urgent = req("U", 0, 0, "15:00", "15:20", duration=60)
    event = Event(type=EventType.URGENT, time="12:00", request=urgent)

    optimal = apply_event(base, event, ctx)
    stable = apply_event(base, event, ctx, variant="stable")

    assert optimal.plan.unassigned == [] and stable.plan.unassigned == []
    assert [move.request_id for move in optimal.last_diff.moved] == ["B"]
    assert stable.last_diff.moved == []
    assert routes(stable.plan)[near] == ["M1", "B"]
    assert stable.plan.metrics.total_km > optimal.plan.metrics.total_km


def test_events_that_do_not_break_the_plan_ignore_the_variant(solves):
    ctx = context()
    base = new_session(ctx)
    cancel = Event(type=EventType.CANCEL, time="09:30", request_id="R2")
    assert apply_event(base, cancel, ctx, variant="keep").plan == apply_event(base, cancel, ctx).plan


def _plan(unassigned, engineers, km, late=0, holder=None):
    """План с метриками для сравнения вариантов; holder — бригада (не E1), к которой попала заявка U1."""
    visits = [
        Visit(request_id=f"L{k}", arrival=600, start=600, end=630, leg_km=1, leg_min=5, late_min=5)
        for k in range(late)
    ]
    routes = [Route(engineer_id="E1", visits=visits, total_km=km, total_travel_min=0)]
    if holder is not None:
        urgent = Visit(request_id="U1", arrival=600, start=600, end=630, leg_km=1, leg_min=5, late_min=0)
        routes.append(Route(engineer_id=holder, visits=[urgent], total_km=0, total_travel_min=0))
    return Plan(
        solver="ortools",
        routes=routes,
        unassigned=[],
        metrics=Metrics(
            engineers_used=engineers, km_per_engineer={}, total_km=km, assigned=0, unassigned=unassigned
        ),
    )


def _diff(before, after, moved):
    return PlanDiff(
        moved=[
            DiffMove(request_id=f"M{k}", from_engineer_id="E1", to_engineer_id="E2") for k in range(moved)
        ],
        metrics_before=before.metrics,
        metrics_after=after.metrics,
    )


def test_choice_recommends_by_clients_then_brigades_then_moves_then_km_and_explains_differences():
    before = _plan(0, 5, 100.0)
    optimal = _plan(0, 5, 110.0)
    stable = _plan(0, 6, 130.5)
    keep = _plan(2, 5, 100.0, late=1)
    outcomes = [
        Outcome("optimal", optimal, _diff(before, optimal, 5)),
        Outcome("stable", stable, _diff(before, stable, 1)),
        Outcome("keep", keep, _diff(before, keep, 0)),
    ]
    event = Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id="E1")

    choice = build_choice("tl_1", event, before, outcomes, None)

    by_variant = {option.variant: option for option in choice.variants}
    assert [option.variant for option in choice.variants] == ["optimal", "stable", "keep"]
    assert [option.recommended for option in choice.variants] == [True, False, False]
    assert by_variant["optimal"].title == "Оптимально по дню"
    assert by_variant["keep"].summary == "Оставить маршруты как есть"
    assert (by_variant["keep"].late, by_variant["optimal"].moved) == (1, 5)
    assert (choice.metrics_before, choice.late_before, choice.current) == (before.metrics, 0, None)
    # Рекомендованный сравнивается со следующим по ключу («Минимум перестановок»).
    assert by_variant["optimal"].pros == ["на 1 бригаду меньше", "на 20,5 км меньше"]
    assert by_variant["optimal"].cons == ["на 4 заявки больше переезжает к другим бригадам"]
    assert by_variant["stable"].pros == ["на 4 заявки меньше переезжает к другим бригадам"]
    assert by_variant["stable"].cons == ["на 1 бригаду больше", "на 20,5 км больше"]
    assert by_variant["keep"].pros == ["на 5 заявок меньше переезжает к другим бригадам", "на 10,0 км меньше"]
    assert by_variant["keep"].cons == ["на 3 клиента без инженера или с опозданием больше"]


def test_choice_ties_go_to_the_earlier_strategy():
    before = _plan(0, 5, 100.0)
    same = _plan(0, 5, 100.0)
    outcomes = [Outcome(variant, same, _diff(before, same, 0)) for variant in ("keep", "stable", "optimal")]
    urgent = Event(type=EventType.URGENT, time="12:00", request=req("U1", 0, 0, "12:00", "13:00"))
    choice = build_choice("tl_2", urgent, before, outcomes, "stable")
    assert [option.variant for option in choice.variants if option.recommended] == ["optimal"]
    assert choice.current == "stable"
    assert all(option.pros == [] and option.cons == [] for option in choice.variants)


def test_choice_for_a_reassignment_offers_to_insert_into_the_route():
    before = _plan(0, 5, 100.0)
    outcomes = [Outcome(variant, before, _diff(before, before, 1)) for variant in VARIANTS]
    event = Event(type=EventType.REQUEST_REASSIGNED, time="12:00", request_id="R1", engineer_id="E2")

    choice = build_choice("tl_3", event, before, outcomes, None)

    assert [(option.title, option.summary) for option in choice.variants] == [
        ("Оптимально по дню", "Пересчитать остаток дня целиком"),
        ("Минимум перестановок", "Чужие маршруты почти не трогаем"),
        ("Вставить в маршрут", "Бригада пропускает, на что не успевает, остальные маршруты как есть"),
    ]
    assert variant_title("keep", EventType.URGENT) == "Ничего не менять"
    assert variant_summary("keep", EventType.URGENT) == "Оставить маршруты как есть"
    # Бригаду выбирают только у срочной заявки: переназначение её и так называет.
    assert choice.assignable is False
    assert all(option.request_engineer_id is None for option in choice.variants)


def test_choice_adds_giving_the_request_to_a_brigade_as_a_fourth_option_priced_against_the_optimum():
    before = _plan(0, 5, 100.0)
    optimal = _plan(0, 5, 110.0, holder="E7")
    stable = _plan(0, 5, 100.0, holder="E8")
    keep = _plan(1, 5, 100.0)
    given = _plan(0, 6, 140.0, holder="E9")
    outcomes = [
        Outcome("optimal", optimal, _diff(before, optimal, 2)),
        Outcome("stable", stable, _diff(before, stable, 1)),
        Outcome("keep", keep, _diff(before, keep, 0)),
        Outcome(assign_variant("E9"), given, _diff(before, given, 2)),
    ]
    event = Event(type=EventType.URGENT, time="12:00", request=req("U1", 0, 0, "12:00", "13:00"))

    choice = build_choice("tl_1", event, before, outcomes, None, {"E9": "Бригада Зверев"})

    assert choice.assignable is True
    assert [option.variant for option in choice.variants] == ["optimal", "stable", "keep", "assign:E9"]
    # Рекомендация считается по трём базовым вариантам: «Минимум перестановок» переносит меньше заявок.
    assert [option.recommended for option in choice.variants] == [False, True, False, False]
    assert [option.compared_to for option in choice.variants] == ["stable", "optimal", "stable", "optimal"]
    assert [option.request_engineer_id for option in choice.variants] == ["E7", "E8", None, "E9"]
    chosen = choice.variants[3]
    assert (chosen.title, chosen.summary) == ("Отдать: Бригада Зверев", "Выбор диспетчера")
    # Цена решения считается от «Оптимально по дню», хотя рекомендован другой вариант.
    assert (chosen.pros, chosen.cons) == ([], ["на 1 бригаду больше", "на 30,0 км больше"])


# --- Оборудование в вариантах без решателя ---------------------------------------------------------------------


def _equipment_day(stock):
    """День из трёх заявок с оборудованием и бригады с запасом stock; прежний маршрут бригады — R0."""
    requests = [req(f"R{k}", 1, k / 10, "10:00", "18:00", duration=30, equipment=True) for k in range(3)]
    problem = problem_of(requests, [eng("E1", equipment_stock=stock)])
    return replace(problem, previous_order={"E1": ["R0", "R1"]}, previous_assignment={"R0": "E1", "R1": "E1"})


def test_keep_shows_that_the_previous_route_no_longer_has_equipment():
    """«Ничего не менять» прогоняет прежний маршрут как есть: нехватка оборудования видна нарушением."""
    plan = keep_plan(_equipment_day(1))
    assert routes(plan)["E1"] == ["R0", "R1"]
    assert plan.metrics.violations == 1
    assert "не осталось оборудования" in plan.violations[0]


def test_insert_skips_the_own_request_when_the_brigade_has_one_unit_left():
    """«Вставить в маршрут»: бригада с одной единицей берёт переназначенную заявку и пропускает свою."""
    problem = replace(_equipment_day(1), previous_order={"E1": ["R0"]}, previous_assignment={"R0": "E1"})
    plan = insert_plan(problem, "R1", "E1")
    assert routes(plan)["E1"] == ["R1"]
    assert [item.request_id for item in plan.unassigned] == ["R0", "R2"]
    assert plan.metrics.violations == 0


def test_skipped_because_of_equipment_is_not_explained_by_time():
    """Бригада с запасом 2 берёт третью заявку с оборудованием и пропускает свою: дело не во времени.

    Все окна до 18:00, маршрут без опозданий и переработок (plan.violations пуст), так что «чтобы успеть
    к заявке R2» было бы неправдой: единицы оборудования кончились, а днём их не берут.
    """
    plan = insert_plan(_equipment_day(2), "R2", "E1")
    assert (routes(plan)["E1"], plan.violations) == (["R0", "R2"], [])
    assert [(item.request_id, item.reason_text) for item in plan.unassigned] == [
        (
            "R1",
            "Вариант «Вставить в маршрут»: у Инженер E1 не осталось оборудования на эту заявку: "
            "утром бригада взяла 2 ед.",
        )
    ]
