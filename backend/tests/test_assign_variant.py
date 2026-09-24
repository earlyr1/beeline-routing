"""Вариант «отдать заявку конкретной бригаде» у события об одной заявке: токены стратегии, закрепление и честный план."""

import pytest

from app.domain.enums import EventType, RequestStatus, Skill
from app.domain.models import Event
from app.planning.facts import assign_allowed, assignable, event_facts
from app.planning.session import apply_event
from app.planning.variants import ASSIGN_PREFIX, VARIANTS, assign_variant, assigned_engineer
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


def test_a_request_event_can_be_given_to_a_chosen_brigade_unless_it_takes_the_request_away():
    """Отдать бригаде можно заявку события об одной заявке, которая после события остаётся в плане.

    События об инженере двигают целую пачку заявок, отменённую заявку отдавать некому, а переназначение само
    называет бригаду: в событии осталась бы одна бригада, а в плане заявка стояла бы у другой.
    """
    allowed_types = {EventType.URGENT, EventType.REQUEST_UPDATED, EventType.RESTORE}
    request = req("R1", 0, 0, "13:00", "17:00")
    for event_type in EventType:
        event = Event.model_construct(type=event_type, time=780, request_id="R1", request=request)
        assert assign_allowed(event) is (event_type in allowed_types), event_type
        # По заявкам после события: та же проверка и заявка в работе дня.
        assert assignable(event, [request]) is (event_type in allowed_types), event_type
        cancelled = request.model_copy(update={"status": RequestStatus.CANCELLED})
        assert assignable(event, [cancelled]) is False, event_type


def test_an_edit_of_a_request_cancelled_earlier_cannot_be_given_to_a_brigade(solves):
    """Правка отменённой заявки не отклоняется, но заявка после неё не в работе: «отдать бригаде» — это
    «Оптимально по дню», закреплять её не за кем."""
    ctx = context()
    base = apply_event(new_session(ctx), cancel("R3", "11:00"), ctx)
    edited = base.request("R3").model_copy(update={"duration_min": 45})
    event = Event(type=EventType.REQUEST_UPDATED, time="12:00", request_id="R3", request=edited)

    after = event_facts(base, event, ctx)
    given = apply_event(base, event, ctx, variant=assign_variant("E2"))

    assert assign_allowed(event) and not assignable(after.event, after.requests)
    assert given.request("R3").fixed_engineer_id is None
    assert given.plan == apply_event(base, event, ctx).plan


def test_a_reassigned_request_is_not_given_to_another_brigade_than_the_event_names(solves):
    """«Отдать бригаде» у переназначения — «Оптимально по дню»: заявка у той бригады, которую назвало событие."""
    ctx = context()
    base = new_session(ctx)
    assert owner(base.plan, "R3") == "E1"
    event = Event(type=EventType.REQUEST_REASSIGNED, time="12:00", request_id="R3", engineer_id="E2")

    given = apply_event(base, event, ctx, variant=assign_variant("E1"))

    assert given.events[-1].event.engineer_id == "E2"
    assert (owner(given.plan, "R3"), given.request("R3").fixed_engineer_id) == ("E2", "E2")


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


def test_an_edited_request_can_be_given_to_a_chosen_brigade(solves):
    """Правка заявки — тоже событие об одной заявке: её можно отдать названной бригаде, а отмену — нет."""
    ctx = context()
    base = new_session(ctx)
    assert owner(base.plan, "R3") == "E1"
    edited = base.request("R3").model_copy(update={"duration_min": 45})
    event = Event(type=EventType.REQUEST_UPDATED, time="12:00", request_id="R3", request=edited)

    given = apply_event(base, event, ctx, variant=assign_variant("E2"))

    assert (owner(given.plan, "R3"), given.request("R3").fixed_engineer_id) == ("E2", "E2")
    # У отмены «отдать бригаде» — просто «Оптимально по дню»: закреплять отменённую заявку не за кем.
    cancelled = apply_event(base, cancel("R3", "12:00"), ctx, variant=assign_variant("E2"))
    assert cancelled.request("R3").fixed_engineer_id is None
    assert cancelled.plan == apply_event(base, cancel("R3", "12:00"), ctx).plan


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
