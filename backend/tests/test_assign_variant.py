"""Вариант «отдать заявку конкретной бригаде» у срочной заявки: токены стратегии, закрепление и честный план."""

import pytest

from app.domain.enums import EventType, Skill
from app.domain.models import Event
from app.planning.session import apply_event
from app.planning.variants import ASSIGN_PREFIX, VARIANTS, assign_variant, assigned_engineer, is_assignable
from tests.helpers import eng, req
from tests.planning_helpers import context, new_session, other_engineer, routes
from tests.timeline_helpers import cancel, fcfs_solves


@pytest.fixture
def solves(monkeypatch):
    return fcfs_solves(monkeypatch)


def urgent(request_id="U1", time="12:00", **extra):
    return Event(type=EventType.URGENT, time=time, request=req(request_id, 0, 0, "13:00", "17:00", **extra))


def owner(plan, request_id):
    return next((engineer_id for engineer_id, visits in routes(plan).items() if request_id in visits), None)


def test_only_an_urgent_request_can_be_given_to_a_chosen_brigade():
    """Остальные «ломающие» события двигают целую пачку заявок: «отдать заявку бригаде» там бессмысленно."""
    for event_type in EventType:
        event = Event.model_construct(type=event_type, time=780)
        assert is_assignable(event) is (event_type == EventType.URGENT)


def test_assign_tokens_name_the_brigade_and_the_three_strategies_stay_as_they_were():
    assert assign_variant("E07") == f"{ASSIGN_PREFIX}E07" == "assign:E07"
    assert assigned_engineer("assign:E07") == "E07"
    assert [assigned_engineer(variant) for variant in VARIANTS] == [None, None, None]
    # Строка без номера бригады стратегией не считается: до решателя такой выбор не доходит.
    assert assigned_engineer("assign:") is None and assigned_engineer(None) is None


def test_the_request_goes_to_the_chosen_brigade_even_though_the_optimum_wanted_another_one():
    """Настоящий OR-Tools: заявка встаёт к названной бригаде, а не туда, куда её кладёт оптимум."""
    ctx = context()
    base = new_session(ctx)
    event = urgent()
    optimal = apply_event(base, event, ctx)
    chosen = other_engineer(owner(optimal.plan, "U1"))

    given = apply_event(base, event, ctx, variant=assign_variant(chosen))

    assert owner(given.plan, "U1") == chosen
    assert given.request("U1").fixed_engineer_id == chosen
    assert base.request("U1") is None  # исходная сессия не изменилась


def test_the_chosen_brigade_keeps_the_request_at_the_next_event(solves):
    ctx = context()
    base = new_session(ctx)
    given = apply_event(base, urgent(), ctx, variant=assign_variant("E2"))
    assert (owner(given.plan, "U1"), given.request("U1").fixed_engineer_id) == ("E2", "E2")

    later = apply_event(given, cancel("R3", "12:30"), ctx)

    assert later.request("U1").fixed_engineer_id == "E2"
    assert owner(later.plan, "U1") == "E2"


@pytest.mark.parametrize(
    "broken",
    [eng("E2", skills=[Skill.LOCAL]), eng("E2", available=False, unavailable_from="09:00")],
    ids=["no_skill", "unavailable"],
)
def test_a_brigade_that_cannot_take_the_request_gives_an_honest_plan_and_not_a_refusal(solves, broken):
    """Событие принято при любой бригаде, но закрепление за той, что заявку взять не может, снимается сразу.

    Иначе оно жило бы ровно один шаг: на следующем событии те же правила его снимают, и утверждённый
    диспетчером план менялся бы сам.
    """
    ctx = context()
    base = new_session(ctx, engineers=[eng("E1"), broken])
    event = urgent(skill=Skill.EMERGENCY)
    assert owner(apply_event(base, event, ctx).plan, "U1") == "E1"

    given = apply_event(base, event, ctx, variant=assign_variant("E2"))

    assert owner(given.plan, "U1") == "E1"
    assert given.request("U1").fixed_engineer_id is None
    # План из окна остаётся и после следующего события: снимать закрепление уже нечего.
    later = apply_event(given, cancel("R3", "12:30"), ctx)
    assert (owner(later.plan, "U1"), later.request("U1").fixed_engineer_id) == ("E1", None)


def test_a_brigade_that_simply_does_not_make_it_keeps_the_request_and_the_price_stays(solves):
    """Цена решения: бригада с навыком, но без времени держит заявку у себя, и на следующем событии тоже."""
    ctx = context()
    base = new_session(ctx, engineers=[eng("E1"), eng("E2", shift=("09:00", "12:30"))])
    given = apply_event(base, urgent(), ctx, variant=assign_variant("E2"))

    assert owner(given.plan, "U1") is None
    assert "U1" in {item.request_id for item in given.plan.unassigned}
    assert given.request("U1").fixed_engineer_id == "E2"

    later = apply_event(given, cancel("R3", "12:30"), ctx)

    assert (owner(later.plan, "U1"), later.request("U1").fixed_engineer_id) == (None, "E2")
