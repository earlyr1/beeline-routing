"""Обед по выбору: без обеда день планируется как до появления обеда, лимит OR-Tools на весь день зависит от обеда."""

import pytest

from app.domain.enums import EventType, ReasonCode
from app.domain.models import Event
from app.geo.matrix import TrafficProfile, TravelModel
from app.planning import session as session_module
from app.planning.explain import build_explanation
from app.planning.session import PlanningContext, apply_event
from app.settings import DEFAULT_SOLVER_TIME_LIMIT_S
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import OrToolsSolver
from app.solvers.problem import make_problem
from tests.api_helpers import sample_bundle, upload
from tests.helpers import eng, req
from tests.planning_helpers import EXACT_TRAVEL_LEVEL, context, new_session, routes

# Окно R2 — 10 минут на приезд, а бригада попадает в 13:31: причина называет это первым, а не общим
# «не помещается» (test_reasons.py). Времена, метрики и код причины при этом те же, что до появления обеда, —
# ради них тест и написан.
NO_LUNCH_REASON = (
    "Окно приезда 13:00–13:10 — всего 10 мин, бригада должна попасть ровно в них, а работ на 150 мин. "
    "Даже без других заявок Инженер E1 начнёт не раньше 13:31 и закончит в 16:01 (смена до 18:00)."
)


def all_lunches(*plans):
    return [route.lunch for plan in plans for route in plan.routes]


def visit_times(plan):
    return {
        route.engineer_id: [(v.request_id, v.arrival, v.start, v.end, v.pinned) for v in route.visits]
        for route in plan.routes
    }


# Задача и солверы


def two_long_visits(lunch):
    """Одному инженеру обе заявки без обеда успеть можно (11:00–13:00 и 13:01–15:31), с обедом нельзя."""
    requests = [
        req("R1", 1, 0, "11:00", "11:10", duration=120),
        req("R2", 1, 0.1, "13:00", "13:10", duration=150),
    ]
    return make_problem(
        requests, [eng("E1"), eng("E2")], model=TravelModel(), traffic=TrafficProfile({}), lunch=lunch
    )


def test_problem_without_lunch_has_no_lunch_window_and_ortools_model_has_no_break():
    with_lunch, without = two_long_visits(True), two_long_visits(False)
    assert with_lunch.lunch and not without.lunch
    assert all(with_lunch.lunch_window(state) is not None for state in with_lunch.states)
    assert all(without.lunch_window(state) is None for state in without.states)

    solver = OrToolsSolver(time_limit_s=1)
    # С перерывом в модели заявки расходятся по двум инженерам, без перерыва обе у одного.
    assert sorted(map(len, solver._solve_model(with_lunch, with_lunch.states, ["R1", "R2"]))) == [1, 1]
    assert sorted(map(len, solver._solve_model(without, without.states, ["R1", "R2"]))) == [0, 2]

    for plan in (FcfsSolver().solve(without), solver.solve(without)):
        # Инженеры стартуют из одной точки: кому из них достанутся обе заявки, для оптимизатора всё равно.
        assert sorted(routes(plan).values()) == [[], ["R1", "R2"]], plan.solver
        assert all_lunches(plan) == [None, None]
        assert plan.unassigned == [] and plan.violations == []


# Сессия


def lunch_sensitive_requests():
    """R3 занимает 09:30–16:30 без перерыва, R1 и R2 подряд 11:00–15:31: с обедом так спланировать нельзя."""
    return [
        req("R1", 1, 0, "11:00", "11:10", duration=120),
        req("R2", 1, 0.1, "13:00", "13:10", duration=150),
        req("R3", -1, 0, "09:30", "09:40", duration=420),
    ]


def lunch_sensitive_session(ctx, lunch_enabled):
    return new_session(
        ctx=ctx,
        requests=lunch_sensitive_requests(),
        workload_level=EXACT_TRAVEL_LEVEL,
        lunch_enabled=lunch_enabled,
    )


def test_session_without_lunch_plans_exactly_as_before_lunch_feature():
    """Ожидаемые значения получены кодом до появления обеда (коммит 6331777, уровень «На пределе»)."""
    ctx = context()
    session = lunch_sensitive_session(ctx, lunch_enabled=False)

    assert session.lunch_enabled is False and session.problem.lunch is False
    day = {
        "E1": [("R1", 544, 660, 780, False), ("R2", 781, 781, 931, False)],
        "E2": [("R3", 544, 570, 990, False)],
    }
    for plan in (session.plan, session.baseline):
        assert visit_times(plan) == day, plan.solver
        metrics = plan.metrics
        assert (metrics.engineers_used, metrics.total_km) == (2, 2.73)
        assert (metrics.assigned, metrics.unassigned) == (3, 0)
        assert plan.unassigned == [] and plan.violations == []
    assert all_lunches(session.plan, session.baseline) == [None] * 4
    assert build_explanation(session.problem, session.plan, session.request("R1")).factors == [
        "Другие инженеры взять заявку не могут: причины указаны в списке альтернатив.",
        "В плане задействовано инженеров: 2, суммарный пробег 2.7 км.",
    ]

    updated = apply_event(
        session, Event(type=EventType.ENGINEER_DELAYED, time="11:30", engineer_id="E1", delay_min=30), ctx
    )

    # События сохраняют выбор сессии: перепланирование тоже без обеда.
    assert updated.lunch_enabled is False and updated.problem.lunch is False
    assert updated.problem.pinned_lunch == {}
    for plan in (updated.plan, updated.baseline):
        assert visit_times(plan) == {"E1": [("R1", 544, 660, 810, True)], "E2": [("R3", 544, 570, 990, True)]}
        [lost] = plan.unassigned
        assert (lost.request_id, lost.reason_code, lost.reason_text) == (
            "R2",
            ReasonCode.DOES_NOT_FIT,
            NO_LUNCH_REASON,
        )
    assert all_lunches(updated.plan, updated.baseline) == [None] * 4
    [late] = updated.last_diff.delay_forecast.late_without_replan
    assert (late.request_id, late.planned_start, late.forecast_start, late.late_min) == ("R2", 781, 811, 21)
    r1 = updated.request("R1")
    assert r1 is not None
    assert build_explanation(updated.problem, updated.plan, r1).factors == [
        "Работа уже началась к моменту последнего события, поэтому заявка не переназначается."
    ]


def test_same_day_with_lunch_is_planned_differently():
    """Контроль сценария выше: с обедом R3 не помещается ни у кого, R1 и R2 расходятся по двум инженерам."""
    session = lunch_sensitive_session(context(), lunch_enabled=True)
    assert session.lunch_enabled is True
    for plan in (session.plan, session.baseline):
        assert sorted(map(len, routes(plan).values())) == [1, 1], plan.solver
        [lost] = plan.unassigned
        assert lost.request_id == "R3" and "с учётом обеда" in lost.reason_text
        assert all(route.lunch is not None for route in plan.routes)


def test_events_keep_lunch_enabled_session_flag():
    ctx = context()
    session = new_session(ctx=ctx)
    assert session.lunch_enabled is True
    updated = apply_event(session, Event(type=EventType.CANCEL, time="13:00", request_id="R3"), ctx)
    assert updated.lunch_enabled is True and updated.problem.lunch is True
    assert any(route.lunch is not None for route in updated.plan.routes)


# Лимит времени OR-Tools


@pytest.fixture
def solver_limits(monkeypatch):
    """Лимиты, с которыми сессия создавала OrToolsSolver, по порядку решений. Сам поиск идёт 1 секунду."""
    seen = []

    class SpySolver(OrToolsSolver):
        def __init__(self, time_limit_s=DEFAULT_SOLVER_TIME_LIMIT_S, weights=None):
            seen.append(time_limit_s)
            super().__init__(time_limit_s=1, weights=weights)

    monkeypatch.setattr(session_module, "OrToolsSolver", SpySolver)
    return seen


@pytest.mark.parametrize(("lunch_enabled", "full_day"), [(True, 30), (False, 5)])
def test_full_day_limit_depends_on_lunch_and_event_replan_keeps_five_seconds(
    solver_limits, lunch_enabled, full_day
):
    ctx = PlanningContext(model=TravelModel(), traffic=TrafficProfile({}))
    session = new_session(ctx=ctx, lunch_enabled=lunch_enabled)
    updated = apply_event(session, Event(type=EventType.CANCEL, time="13:00", request_id="R2"), ctx)
    apply_event(updated, Event(type=EventType.RESTORE, time="13:10", request_id="R2"), ctx)
    assert solver_limits == [full_day, 5, 5]


def test_time_limits_come_from_planning_context(solver_limits):
    ctx = context(time_limit_s=2, time_limit_lunch_s=7)
    for lunch_enabled in (True, False):
        session = new_session(ctx=ctx, lunch_enabled=lunch_enabled)
        apply_event(session, Event(type=EventType.CANCEL, time="13:00", request_id="R2"), ctx)
    assert solver_limits == [7, 2, 2, 2]


def test_api_full_day_solves_use_lunch_limit_and_events_use_replan_limit(api, solver_limits):
    client, _ = api(solver_time_limit_s=5, solver_time_limit_lunch_s=15)
    base = f"/api/datasets/{upload(client, 'bundle.json', sample_bundle().model_dump_json().encode())}"
    cancel = {"type": "cancel", "time": "13:00", "request_id": "R2"}

    assert solver_limits == [15]  # предподсчёт загрузки: обед по умолчанию включён
    assert client.post(f"{base}/plan", json={"lunch": False}).status_code == 200
    assert client.post(f"{base}/events", json=cancel).status_code == 200
    assert client.post(f"{base}/plan", json={"lunch": True}).status_code == 200
    assert client.post(f"{base}/events", json=cancel).status_code == 200
    assert client.post(f"{base}/plan").status_code == 200  # «Пересчитать с нуля» после события

    assert solver_limits == [15, 5, 5, 15, 5, 15]


# POST /plan


def _ready_dataset(client):
    dataset_id = upload(client, "bundle.json", sample_bundle().model_dump_json().encode())
    assert client.get(f"/api/datasets/{dataset_id}").json()["status"] == "ready"
    return f"/api/datasets/{dataset_id}"


def _json_lunches(state):
    return [route["lunch"] for plan in (state["plan"], state["baseline"]) for route in plan["routes"]]


def _choice(state):
    """Версия плана и выбор дня: уровень нагрузки и обед."""
    return state["version"], state["workload_level"], state["lunch_enabled"]


def test_plan_lunch_choice_rebuilds_day_and_omitted_fields_keep_session_values(api):
    client, _ = api()
    base = _ready_dataset(client)

    precomputed = client.post(f"{base}/plan").json()
    assert _choice(precomputed) == (1, 1, True)
    assert any(lunch is not None for lunch in _json_lunches(precomputed))
    # Те же значения без событий: предподсчитанный план без изменений.
    assert client.post(f"{base}/plan", json={"lunch": True}).json() == precomputed
    assert client.post(f"{base}/plan", json={"workload_level": 1, "lunch": True}).json() == precomputed
    assert client.post(f"{base}/plan", json={"lunch": None}).json() == precomputed

    no_lunch = client.post(f"{base}/plan", json={"lunch": False}).json()
    assert _choice(no_lunch) == (2, 1, False)
    assert no_lunch["events"] == [] and _json_lunches(no_lunch) == [None] * 4
    for body in (None, {}, {"lunch": False}, {"workload_level": 1}):
        assert client.post(f"{base}/plan", json=body).json() == no_lunch, body
    assert client.get(f"{base}/state").json()["lunch_enabled"] is False

    # Смена только уровня оставляет выбор обеда сессии.
    calm = client.post(f"{base}/plan", json={"workload_level": 0}).json()
    assert _choice(calm) == (3, 0, False)

    event = client.post(f"{base}/events", json={"type": "cancel", "time": "13:00", "request_id": "R2"})
    assert event.status_code == 200, event.text
    replanned = event.json()
    assert _choice(replanned) == (4, 0, False)
    assert _json_lunches(replanned) == [None] * 4
    assert all(route["lunch"] is None for route in replanned["previous_plan"]["routes"])

    # После событий день пересобирается и с теми же значениями.
    rebuilt = client.post(f"{base}/plan", json={"lunch": False}).json()
    assert _choice(rebuilt) == (5, 0, False)
    assert rebuilt["events"] == []

    both = client.post(f"{base}/plan", json={"workload_level": 2, "lunch": True}).json()
    assert _choice(both) == (6, 2, True)
    assert any(lunch is not None for lunch in _json_lunches(both))


def test_plan_rejects_non_boolean_lunch(api):
    client, _ = api()
    base = _ready_dataset(client)
    for value in ("нет", 0, 1, "false"):
        response = client.post(f"{base}/plan", json={"lunch": value})
        assert response.status_code == 422, value
        assert response.json() == {"detail": "Некорректный запрос: lunch: нужно «да» или «нет»"}, value
    state = client.get(f"{base}/state").json()
    assert (state["version"], state["lunch_enabled"]) == (1, True)
