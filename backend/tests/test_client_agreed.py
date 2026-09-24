"""Событие «Коммуникация» (client_agreed): что клиенту сказали по телефону, становится фактом дня.

Названное окно — окно заявки, и решатель обязан его держать; «сегодня не приедем» переносит заявку: решатель её
не получает, а план показывает среди заявок без инженера и считает в «Не назначено».
"""

import pytest

from app.domain.enums import EventType, ReasonCode, RequestStatus
from app.domain.models import Event, TimeWindow
from app.domain.timeutil import parse_hhmm
from app.planning.explain import build_explanation
from app.planning.facts import Agreement, agreement, asap_window, assignable, event_facts
from app.planning.session import EventRejected, apply_event
from app.planning.variants import VARIANTS, needs_choice
from app.solvers.problem import POSTPONED_TEXT
from tests.planning_helpers import context, day_requests, new_session, routes
from tests.timeline_helpers import cancel


def agreed(request_id, time, window=None, asap=False):
    told = None if window is None else TimeWindow(start=window[0], end=window[1], asap=asap)
    return Event(type=EventType.CLIENT_AGREED, time=time, request_id=request_id, agreed_window=told)


def start_of(plan, request_id):
    return next(
        visit.start for route in plan.routes for visit in route.visits if visit.request_id == request_id
    )


def planned(plan):
    return {request_id for sequence in routes(plan).values() for request_id in sequence}


def test_the_window_named_to_the_client_holds_through_the_next_replan():
    ctx = context()
    session = new_session(ctx)
    # Утром R3 стоит в своём окне 15:00–17:00; клиенту назвали 16:30–17:30.
    assert start_of(session.plan, "R3") < parse_hhmm("16:30")

    told = apply_event(session, agreed("R3", "09:00", ("16:30", "17:30")), ctx)
    window = (told.request("R3").window_start, told.request("R3").window_end, told.request("R3").asap)
    assert window == (parse_hhmm("16:30"), parse_hhmm("17:30"), False)
    assert parse_hhmm("16:30") <= start_of(told.plan, "R3") <= parse_hhmm("17:30")

    # Следующее событие пересчитывает остаток дня, а обещание клиенту остаётся в силе.
    later = apply_event(told, cancel("R1", "09:30"), ctx)
    assert (later.request("R3").window_start, later.request("R3").window_end) == window[:2]
    assert parse_hhmm("16:30") <= start_of(later.plan, "R3") <= parse_hhmm("17:30")


def test_keep_holds_the_named_window_too():
    ctx = context()
    session = new_session(ctx)
    told = apply_event(session, agreed("R3", "09:00", ("16:30", "17:30")), ctx, variant="keep")
    assert parse_hhmm("16:30") <= start_of(told.plan, "R3") <= parse_hhmm("17:30")
    assert told.plan.metrics.violations == 0


@pytest.mark.parametrize("variant", VARIANTS)
def test_a_postponed_request_is_planned_by_no_variant_and_counts_as_not_assigned(variant):
    ctx = context()
    session = new_session(ctx)
    assert "R2" in planned(session.plan)

    after = apply_event(session, agreed("R2", "09:00"), ctx, variant=variant)

    assert after.request("R2").status == RequestStatus.POSTPONED
    for plan in (after.plan, after.baseline):  # и у пересчёта, и у базового FCFS «Не назначено» одинаково
        assert "R2" not in planned(plan)
        dropped = {item.request_id: item for item in plan.unassigned}
        assert (dropped["R2"].reason_code, dropped["R2"].reason_text) == (
            ReasonCode.POSTPONED,
            POSTPONED_TEXT,
        )
        assert plan.metrics.unassigned == len(plan.unassigned) == 1
    assert [(item.request_id, item.reason) for item in after.last_diff.removed] == [("R2", POSTPONED_TEXT)]


def test_postponing_needs_no_choice_and_nothing_can_be_given_to_a_brigade():
    """Правило окна выбора не считает перенесённую заявку поломкой: она без инженера во всех вариантах."""
    ctx = context()
    session = new_session(ctx)
    outcomes = {
        variant: apply_event(session, agreed("R2", "09:00"), ctx, variant=variant) for variant in VARIANTS
    }

    assert not needs_choice(
        {variant: item.plan for variant, item in outcomes.items()}, outcomes["keep"].requests
    )
    facts = event_facts(session, agreed("R2", "09:00"), ctx)
    assert facts.subject_request_id == "R2" and not assignable(facts.event, facts.requests)


def test_a_named_window_brings_a_postponed_request_back():
    """Клиент перезвонил: договорились на окно — заявка снова в работе дня."""
    ctx = context()
    session = apply_event(new_session(ctx), agreed("R2", "09:00"), ctx, variant="keep")
    back = apply_event(session, agreed("R2", "10:00", ("16:00", "18:00")), ctx)
    assert back.request("R2").status == RequestStatus.ACTIVE
    assert parse_hhmm("16:00") <= start_of(back.plan, "R2") <= parse_hhmm("18:00")
    assert "R2" not in {item.request_id for item in back.plan.unassigned}


def test_a_postponed_request_is_explained_by_the_call_not_by_brigades():
    ctx = context()
    session = apply_event(new_session(ctx), agreed("R2", "09:00"), ctx, variant="keep")
    postponed = session.request("R2")
    assert postponed is not None
    explanation = build_explanation(session.problem, session.plan, postponed)
    assert explanation.status == "unassigned"
    assert "сегодня не приедем" in explanation.summary
    assert explanation.constraints == [] and explanation.alternatives == []


def test_a_postponed_request_is_not_reassigned():
    ctx = context()
    session = apply_event(new_session(ctx), agreed("R2", "09:00"), ctx, variant="keep")
    event = Event(type=EventType.REQUEST_REASSIGNED, time="10:00", request_id="R2", engineer_id="E1")
    with pytest.raises(EventRejected, match="Заявка R2 перенесена, назначить её нельзя"):
        apply_event(session, event, ctx)


@pytest.mark.parametrize(
    ("event", "text"),
    [
        (agreed("NOPE", "09:00"), "Заявка NOPE не найдена."),
        (agreed("R2", "12:00", ("10:00", "11:00")), "заканчивается раньше времени события 12:00"),
        (agreed("R2", "09:00", ("16:00", "14:00")), "Конец окна заявки R2 должен быть позже начала."),
        (agreed("R2", "09:00", ("16:00", "16:00")), "Конец окна заявки R2 должен быть позже начала."),
    ],
)
def test_what_cannot_be_agreed(event, text):
    ctx = context()
    with pytest.raises(EventRejected, match=text):
        event_facts(new_session(ctx), event, ctx)


def test_a_cancelled_request_or_one_in_work_cannot_be_agreed_about():
    ctx = context()
    session = new_session(ctx)
    cancelled = apply_event(session, cancel("R2", "09:00"), ctx)
    with pytest.raises(EventRejected, match="Заявка R2 отменена, договариваться с клиентом не о чем."):
        event_facts(cancelled, agreed("R2", "10:00"), ctx)

    started = start_of(session.plan, "R1")
    during = f"{(started + 1) // 60:02d}:{(started + 1) % 60:02d}"
    with pytest.raises(EventRejected, match="Заявка R1 уже в работе с .*, перенести её нельзя."):
        event_facts(session, agreed("R1", during), ctx)


def test_an_asap_window_is_named_in_words_and_set_by_the_server():
    """«Как можно скорее» клиенту называют словами, а окно задаёт сервер, как у изменения заявки: присланные начало
    и конец не используются. Заявка «как можно скорее» держит своё окно, обычная ждёт с времени звонка до конца смен.
    В событии остаётся окно, которое получила заявка: его вкладка «Коммуникации» и считает известным клиенту."""
    ctx = context()
    asap = day_requests()[2].model_copy(update={"asap": True, "window_start": parse_hhmm("09:00")})
    session = new_session(ctx, requests=[*day_requests()[:2], asap])
    anything = ("06:00", "23:59")

    kept = event_facts(session, agreed("R3", "09:00", anything, asap=True), ctx)
    stored = next(request for request in kept.requests if request.id == "R3")
    assert (stored.window_start, stored.window_end, stored.asap) == (asap.window_start, asap.window_end, True)
    assert kept.event.agreed_window == TimeWindow(start=asap.window_start, end=asap.window_end, asap=True)

    started = event_facts(session, agreed("R2", "09:00", anything, asap=True), ctx)
    stored = next(request for request in started.requests if request.id == "R2")
    bounds = asap_window(session.engineers, parse_hhmm("09:00"))
    assert (stored.window_start, stored.window_end, stored.asap) == (
        bounds["window_start"],
        bounds["window_end"],
        True,
    )
    assert started.event.agreed_window == TimeWindow(
        start=bounds["window_start"], end=bounds["window_end"], asap=True
    )


def test_what_the_client_was_told_is_read_only_from_a_call():
    told = TimeWindow(start="16:00", end="18:00")
    assert agreement(agreed("R2", "09:00", ("16:00", "18:00"))) == Agreement("R2", told)
    assert agreement(agreed("R2", "09:00")) == Agreement("R2", None)
    assert agreement(cancel("R2", "09:00")) is None
