"""Изменение заявки посреди дня."""

import pytest

from app.domain.enums import EventType, Priority, RequestStatus, Skill, Transport
from app.domain.models import Event, Request
from app.domain.timeutil import fmt_hhmm
from app.ingest.geocode import GeoResult
from app.planning.explain import build_explanation
from app.planning.session import EventRejected, apply_event, check_event
from tests.helpers import req
from tests.planning_helpers import context, day_requests, new_session, routes, visit_times


def update(request, time="13:00", request_id=None, **extra):
    return Event(
        type=EventType.REQUEST_UPDATED,
        time=time,
        request_id=request_id or request.id,
        request=request,
        **extra,
    )


def edited(session, request_id, **changes):
    return Request.model_validate({**session.request(request_id).model_dump(), **changes})


def visit_of(plan, request_id):
    return next(
        (route.engineer_id, visit)
        for route in plan.routes
        for visit in route.visits
        if visit.request_id == request_id
    )


def test_later_window_and_longer_work_replace_the_request():
    ctx = context()
    session = new_session(ctx=ctx)
    before = session.request("R2")

    updated = apply_event(
        session,
        update(edited(session, "R2", window_start="16:00", window_end="18:00", duration_min=60)),
        ctx,
    )

    stored = updated.request("R2")
    assert (stored.window_start, stored.window_end, stored.duration_min) == (960, 1080, 60)
    assert (stored.lat, stored.lon, stored.geocode_precision) == (before.lat, before.lon, "house")
    assert session.request("R2") == before  # исходная сессия не изменилась
    _, visit = visit_of(updated.plan, "R2")
    assert 960 <= visit.start <= 1080 and visit.end == visit.start + 60 and visit.late_min == 0
    assert [r for r in updated.requests if r.id != "R2"] == [r for r in session.requests if r.id != "R2"]
    assert updated.engineers == session.engineers
    assert sorted(rid for sequence in routes(updated.plan).values() for rid in sequence) == ["R1", "R2", "R3"]

    [applied] = updated.events
    assert applied.event.type == EventType.REQUEST_UPDATED and applied.event.request_id == "R2"
    assert (applied.event.previous_request, applied.event.request) == (before, stored)
    assert (applied.id, applied.version, updated.version, updated.now) == ("ev_1", 2, 2, 780)
    assert updated.baseline.solver == "fcfs"
    assert any(visit.pinned for route in updated.baseline.routes for visit in route.visits)
    assert updated.problem.request("R2") == stored

    explanation = build_explanation(updated.problem, updated.plan, stored)
    assert "в окне 16:00–18:00" in explanation.summary
    window = next(c for c in explanation.constraints if c.name == "Временное окно")
    assert "окно 16:00–18:00" in window.detail


def test_non_editable_fields_come_from_the_stored_request():
    ctx = context()
    requests = day_requests()
    requests[1] = requests[1].model_copy(update={"district": "Таганский", "source_type_bk": "Подключение"})
    session = new_session(ctx=ctx, requests=requests)
    sent = edited(
        session,
        "R2",
        priority=Priority.URGENT,
        skill=Skill.CONNECTION,
        transport_required=Transport.CAR,
        status=RequestStatus.CANCELLED,
        district="Другой",
        source_type_bk="Другой",
        source_type_hd="Другой",
        geocode_precision="none",
    )

    updated = apply_event(session, update(sent, previous_request=sent), ctx)

    stored = updated.request("R2")
    assert (stored.priority, stored.skill, stored.transport_required) == (
        Priority.URGENT,
        Skill.CONNECTION,
        Transport.CAR,
    )
    assert (stored.status, stored.district, stored.source_type_bk, stored.source_type_hd) == (
        RequestStatus.ACTIVE,
        "Таганский",
        "Подключение",
        "",
    )
    assert stored.geocode_precision == "house"
    assert updated.events[0].event.previous_request == session.request("R2")


def test_equipment_flag_is_editable():
    """«Нужно оборудование» в диалоге изменения заявки: флаг информационный, но он меняется и сохраняется."""
    ctx = context()
    session = new_session(ctx=ctx)
    assert session.request("R2").needs_equipment is False

    updated = apply_event(session, update(edited(session, "R2", needs_equipment=True)), ctx)

    assert updated.request("R2").needs_equipment is True
    assert updated.events[0].event.previous_request.needs_equipment is False


def street_level_session(ctx):
    requests = day_requests()
    requests[1] = requests[1].model_copy(update={"district": "Таганский", "geocode_precision": "street"})
    return new_session(ctx=ctx, requests=requests)


def test_map_point_moves_the_request_without_geocoding():
    calls = []
    ctx = context(geocode=lambda address, district: calls.append(address))
    session = street_level_session(ctx)
    sent = edited(session, "R2", lat=55.7601, lon=37.6202, address="Точка на карте")

    updated = apply_event(session, update(sent), ctx)

    stored = updated.request("R2")
    assert (stored.lat, stored.lon, stored.geocode_precision, stored.address) == (
        55.7601,
        37.6202,
        "house",
        "Точка на карте",
    )
    assert calls == []
    assert (updated.problem.request("R2").lat, updated.problem.request("R2").lon) == (55.7601, 37.6202)


def test_new_address_without_point_is_geocoded():
    calls = []

    def geocode(address, district):
        calls.append((address, district))
        return GeoResult(55.7555, 37.6111, "street", address)

    ctx = context(geocode=geocode)
    session = street_level_session(ctx)
    sent = edited(session, "R2", address="Москва, ул. Новая, 5", lat=None, lon=None)

    stored = check_event(session, update(sent), ctx).request
    assert (stored.lat, stored.lon, stored.geocode_precision) == (55.7555, 37.6111, "street")
    assert calls == [("Москва, ул. Новая, 5", "Таганский")]

    updated = apply_event(session, update(sent), ctx)
    assert updated.request("R2") == stored


def test_same_address_without_point_keeps_coordinates():
    calls = []
    ctx = context(geocode=lambda address, district: calls.append(address))
    session = street_level_session(ctx)
    before = session.request("R2")
    sent = edited(session, "R2", lat=None, lon=None, duration_min=50)

    updated = apply_event(session, update(sent), ctx)

    stored = updated.request("R2")
    assert (stored.lat, stored.lon, stored.geocode_precision, stored.duration_min) == (
        before.lat,
        before.lon,
        "street",
        50,
    )
    assert calls == []


def test_address_not_found_is_rejected():
    missing = context(geocode=lambda address, district: GeoResult(None, None, "none", None))
    session = new_session(ctx=missing)
    sent = edited(session, "R2", address="Нигде, д. 1", lat=None, lon=None)
    text = "Адрес «Нигде, д. 1» не найден на карте. Укажите точку на карте."
    for ctx in (missing, context()):
        with pytest.raises(EventRejected) as rejected:
            apply_event(session, update(sent), ctx)
        assert str(rejected.value) == text


def test_request_update_rejections_are_russian():
    ctx = context()
    session = new_session(ctx=ctx)
    _, started = visit_of(session.plan, "R1")
    later = apply_event(session, Event(type=EventType.CANCEL, time="13:30", request_id="R3"), ctx)
    unknown = req("NOPE", 0, 0, "14:00", "16:00")
    cases = [
        (session, update(unknown), "Заявка NOPE не найдена."),
        (
            session,
            update(edited(session, "R1", duration_min=90)),
            f"Заявка R1 уже в работе с {fmt_hhmm(started.start)}, изменить её нельзя.",
        ),
        (
            session,
            update(edited(session, "R2", window_start="11:00", window_end="12:30")),
            "Окно заявки R2 заканчивается в 12:30, это раньше времени события 13:00.",
        ),
        (
            session,
            update(edited(session, "R2", window_start="14:00", window_end="14:00")),
            "Конец окна заявки R2 должен быть позже начала.",
        ),
        (session, update(edited(session, "R2")), "В заявке R2 ничего не изменилось."),
        (
            later,
            update(edited(later, "R2", duration_min=45), time="13:00"),
            "Время события 13:00 раньше текущего времени плана 13:30.",
        ),
    ]
    assert started.start < 780
    for current, event, text in cases:
        with pytest.raises(EventRejected) as rejected:
            apply_event(current, event, ctx)
        assert str(rejected.value) == text
        with pytest.raises(EventRejected) as checked:
            check_event(current, event, ctx)
        assert str(checked.value) == text


def test_check_event_fills_previous_request_without_replanning():
    ctx = context()
    session = new_session(ctx=ctx)
    stored = check_event(session, update(edited(session, "R3", duration_min=75)), ctx)
    assert (stored.previous_request, stored.request.duration_min) == (session.request("R3"), 75)
    assert session.request("R3").duration_min == 30 and session.version == 1


def two_trips():
    """В 09:05 оба инженера уже в пути: один к A, другой к B на противоположном краю."""
    return [
        req("A", 5, 0, "09:00", "09:30"),
        req("B", -5, 0, "09:00", "09:30"),
        req("C", 5, 1, "11:00", "17:00"),
    ]


def test_edited_request_on_the_way_is_released_and_other_holds_stay():
    ctx = context()
    session = new_session(ctx=ctx, requests=two_trips())
    driver_a, _ = visit_of(session.plan, "A")
    driver_b, _ = visit_of(session.plan, "B")
    assert driver_a != driver_b
    before_b = visit_times(session.plan, driver_b)

    untouched = apply_event(session, update(edited(session, "C", duration_min=40), time="09:05"), ctx)
    assert [v.request_id for v in untouched.problem.pinned[driver_a]] == ["A"]
    assert [v.request_id for v in untouched.problem.pinned[driver_b]] == ["B"]

    updated = apply_event(
        session, update(edited(session, "A", window_start="12:00", window_end="13:00"), time="09:05"), ctx
    )

    assert updated.problem.pinned[driver_a] == []
    assert "A" in updated.problem.open_request_ids
    assert [(v.request_id, v.pinned) for v in updated.problem.pinned[driver_b]] == [("B", False)]
    assert visit_times(updated.plan, driver_b)[0] == before_b[0]
    _, visit = visit_of(updated.plan, "A")
    assert 720 <= visit.start <= 780 and visit.late_min == 0


def test_cancelled_request_can_be_edited_and_stays_cancelled():
    ctx = context()
    cancelled = apply_event(
        new_session(ctx=ctx), Event(type=EventType.CANCEL, time="12:00", request_id="R2"), ctx
    )
    sent = edited(cancelled, "R2", window_start="15:00", window_end="17:00", status=RequestStatus.ACTIVE)

    updated = apply_event(cancelled, update(sent), ctx)

    stored = updated.request("R2")
    assert (stored.status, stored.window_start, stored.window_end) == (RequestStatus.CANCELLED, 900, 1020)
    assert "R2" not in [rid for sequence in routes(updated.plan).values() for rid in sequence]
    assert "R2" not in [item.request_id for item in updated.plan.unassigned]
    assert updated.events[-1].event.previous_request.status == RequestStatus.CANCELLED
    restored = apply_event(updated, Event(type=EventType.RESTORE, time="13:30", request_id="R2"), ctx)
    _, visit = visit_of(restored.plan, "R2")
    assert 900 <= visit.start <= 1020
