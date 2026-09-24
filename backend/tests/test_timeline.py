"""Таймлайн без API: порядок событий, шаги повторного применения, кэш и геокодер."""

import pytest

from app.domain.enums import EventType, RequestStatus
from app.domain.models import Event
from app.ingest.geocode import GeoResult
from app.planning.session import EventRejected, apply_event, geocode_entry
from app.planning.timeline import Timeline, check_known, entry_token, known_requests, replay_step
from app.planning.variants import VARIANTS, assign_variant
from tests.helpers import req
from tests.planning_helpers import busy_engineer, context, new_session
from tests.timeline_helpers import cancel, fcfs_solves, replay_all, restore


@pytest.fixture
def solves(monkeypatch):
    return fcfs_solves(monkeypatch)


def _added(timeline, *events):
    entries = []
    for event in events:
        entry = timeline.create(event)
        timeline.insert(entry)
        entries.append(entry)
    return entries


def test_entries_are_ordered_by_time_then_by_creation_and_ids_are_never_reused():
    timeline = Timeline()
    late, early, tie = _added(timeline, cancel("R2", "14:00"), cancel("R3", "11:00"), restore("R2", "14:00"))

    assert [entry.id for entry in (late, early, tie)] == ["tl_1", "tl_2", "tl_3"]
    assert [entry.id for entry in timeline.entries] == ["tl_2", "tl_1", "tl_3"]
    assert [timeline.applied_count(minute) for minute in (0, 659, 660, 839, 840, 1439)] == [0, 0, 1, 1, 3, 3]

    assert timeline.remove("tl_3") == tie and timeline.remove("tl_3") is None
    assert timeline.create(cancel("R1", "09:00")).id == "tl_4"
    timeline.clear()
    assert timeline.entries == [] and timeline.steps == {}
    assert timeline.create(cancel("R1", "09:00")).id == "tl_5"


def test_entry_keeps_the_event_as_sent_without_backend_fields():
    sent = Event(
        type=EventType.ENGINEER_TRANSPORT_CHANGED,
        time="13:00",
        engineer_id="E1",
        transport="bike",
        previous_transport="public",
    )
    entry = Timeline().create(sent)
    assert entry.event == sent.model_copy(update={"previous_transport": None})
    assert entry.seq == 1 and entry.geo == {} and entry.checked is False


def test_snapshot_after_entries_equals_sequential_application(solves):
    ctx = context()
    base = new_session(ctx)
    timeline = Timeline()
    _added(timeline, cancel("R2", "14:00"), cancel("R3", "11:00"))

    walk = replay_all(timeline, base, ctx)

    sequential = apply_event(apply_event(base, cancel("R3", "11:00"), ctx), cancel("R2", "14:00"), ctx)
    assert walk.done == 2 and walk.session.plan == sequential.plan
    assert walk.session.requests == sequential.requests
    assert walk.session.events == sequential.events
    assert (walk.session.now, walk.session.version) == (sequential.now, sequential.version) == (840, 3)
    assert walk.steps[0].applied == sequential.events[0] and walk.steps[1].applied == sequential.events[1]
    assert timeline.walk(base, 1).session.request("R2").status == RequestStatus.ACTIVE


def test_rejected_entry_is_skipped_with_reason_and_changes_nothing(solves):
    ctx = context()
    base = new_session(ctx)
    timeline = Timeline()
    _added(timeline, cancel("R2", "11:00"), cancel("R2", "12:00"), cancel("R3", "13:00"))

    walk = replay_all(timeline, base, ctx)

    first, rejected, third = walk.steps
    assert (rejected.applied, rejected.reason) == (None, "Заявка R2 уже отменена.")
    assert rejected.session is first.session
    assert [applied.id for applied in walk.session.events] == ["ev_1", "ev_2"]
    assert third.applied.version == 3

    items, ready = timeline.view(base, 780)
    assert ready and [(item.status, item.reason) for item in items] == [
        ("applied", None),
        ("rejected", "Заявка R2 уже отменена."),
        ("applied", None),
    ]
    assert items[0].event == first.applied.event and items[1].event == timeline.entries[1].event
    early, _ = timeline.view(base, 690)
    # Отказ известен и до того, как текущее время дошло до события.
    assert [item.status for item in early] == ["applied", "rejected", "pending"]


def test_view_before_replay_is_pending_and_not_ready():
    base = new_session(context())
    timeline = Timeline()
    _added(timeline, cancel("R2", "11:00"))
    items, ready = timeline.view(base, 780)
    assert not ready and [(item.status, item.event) for item in items] == [("pending", cancel("R2", "11:00"))]


def test_inserting_an_entry_that_is_rejected_keeps_later_steps(solves):
    ctx = context()
    base = new_session(ctx)
    timeline = Timeline()
    _added(timeline, cancel("R2", "11:00"), cancel("R3", "14:00"))
    before = replay_all(timeline, base, ctx)
    solves.clear()

    _added(timeline, cancel("R2", "12:00"))
    walk = replay_all(timeline, base, ctx)

    assert solves == []
    assert walk.steps[1].reason == "Заявка R2 уже отменена."
    assert walk.steps[2] is before.steps[1]


def test_accepted_earlier_entry_invalidates_only_later_steps(solves):
    ctx = context()
    base = new_session(ctx)
    timeline = Timeline()
    _added(timeline, cancel("R3", "11:00"), cancel("R2", "14:00"))
    before = replay_all(timeline, base, ctx)
    assert solves == ["00:00", "11:00", "14:00"]
    solves.clear()

    _added(timeline, restore("R3", "12:00"))
    walk = replay_all(timeline, base, ctx)

    assert solves == ["12:00", "14:00"]
    assert walk.steps[0] is before.steps[0]
    assert walk.session.request("R3").status == RequestStatus.ACTIVE
    assert [applied.version for applied in walk.session.events] == [2, 4, 5]


def test_removing_an_entry_drops_its_steps_and_prune_keeps_the_current_path(solves):
    ctx = context()
    base = new_session(ctx)
    timeline = Timeline()
    _, restored, _ = _added(timeline, cancel("R3", "11:00"), restore("R3", "12:00"), cancel("R2", "14:00"))
    replay_all(timeline, base, ctx)
    assert any(restored.id in {key[1], *key[0]} for key in timeline.steps)

    timeline.remove(restored.id)

    assert not any(restored.id in {key[1], *key[0]} for key in timeline.steps)
    walk = replay_all(timeline, base, ctx)
    assert walk.session.request("R3").status == RequestStatus.CANCELLED
    timeline.prune(walk)
    assert set(timeline.steps) == set(walk.keys) and len(walk.keys) == 2


def test_apply_event_takes_the_version_it_is_given():
    ctx = context()
    updated = apply_event(new_session(ctx), cancel("R2", "13:00"), ctx, version=7)
    assert updated.version == 7 and updated.events[0].version == 7


def _geocoder(calls):
    def geocode(address, district):
        calls.append((address, district))
        return GeoResult(55.7601, 37.6202, "street", address)

    return geocode


def test_request_update_without_point_is_geocoded_once_and_replayed_from_the_answer(solves):
    calls: list[tuple[str, str]] = []
    ctx = context(geocode=_geocoder(calls))
    base = new_session(ctx)
    r2 = base.request("R2").model_copy(update={"district": "Таганский"})
    moved = r2.model_copy(update={"address": "ул. Новая, 1", "lat": None, "lon": None})
    edit = Event(type=EventType.REQUEST_UPDATED, time="12:00", request_id="R2", request=moved)
    requests = known_requests(base, [])

    located, geo = geocode_entry(edit, requests, ctx)

    assert located == edit and calls == [("ул. Новая, 1", base.request("R2").district)]
    assert geo == {"ул. Новая, 1": GeoResult(55.7601, 37.6202, "street", "ул. Новая, 1")}
    timeline = Timeline()
    timeline.insert(timeline.create(located, geo))
    # Раньше по времени адрес заявки меняет точка на карте: повторное применение сравнивает с другим адресом.
    point = r2.model_copy(update={"address": "Точка на карте", "lat": 55.75, "lon": 37.61})
    timeline.insert(
        timeline.create(Event(type=EventType.REQUEST_UPDATED, time="11:00", request_id="R2", request=point))
    )

    walk = replay_all(timeline, base, ctx)

    assert calls == [("ул. Новая, 1", base.request("R2").district)]
    stored = walk.session.request("R2")
    assert [step.reason for step in walk.steps] == [None, None]
    assert (stored.address, stored.lat, stored.lon, stored.geocode_precision) == (
        "ул. Новая, 1",
        55.7601,
        37.6202,
        "street",
    )


def test_request_update_to_the_same_address_is_still_geocoded_and_point_edit_is_not():
    calls: list[tuple[str, str]] = []
    ctx = context(geocode=_geocoder(calls))
    base = new_session(ctx)
    same = base.request("R2").model_copy(update={"lat": None, "lon": None, "duration_min": 60})
    edit = Event(type=EventType.REQUEST_UPDATED, time="12:00", request_id="R2", request=same)
    _, geo = geocode_entry(edit, known_requests(base, []), ctx)
    assert list(geo) == [same.address] and len(calls) == 1

    point = edit.model_copy(update={"request": same.model_copy(update={"lat": 55.7, "lon": 37.6})})
    assert geocode_entry(point, known_requests(base, []), ctx) == (point, {})
    unknown = edit.model_copy(
        update={"request_id": "NOPE", "request": same.model_copy(update={"id": "NOPE"})}
    )
    assert geocode_entry(unknown, known_requests(base, []), ctx) == (unknown, {}) and len(calls) == 1


def test_urgent_request_is_located_at_add_time_and_replay_does_not_geocode(solves):
    calls: list[tuple[str, str]] = []
    ctx = context(geocode=_geocoder(calls))
    base = new_session(ctx)
    urgent = req("U1", 0, 0, "13:00", "15:00").model_copy(update={"lat": None, "lon": None})
    event = Event(type=EventType.URGENT, time="13:00", request=urgent)

    located, geo = geocode_entry(event, known_requests(base, []), ctx)

    assert geo == {} and (located.request.lat, located.request.lon) == (55.7601, 37.6202)
    timeline = Timeline()
    timeline.insert(timeline.create(located, geo, variant="optimal"))
    walk = replay_all(timeline, base, ctx)
    assert len(calls) == 1 and walk.session.request("U1").geocode_precision == "street"


def test_checked_entry_is_replayed_from_its_own_coordinates(solves):
    def fail(address, district):
        raise AssertionError("геокодер не вызывается при повторном применении")

    ctx = context(geocode=fail)
    base = new_session(ctx)
    located = base.request("R2").model_copy(
        update={"address": "ул. Новая, 1", "lat": 55.7601, "lon": 37.6202, "geocode_precision": "street"}
    )
    event = Event(type=EventType.REQUEST_UPDATED, time="12:00", request_id="R2", request=located)
    timeline = Timeline()
    timeline.insert(timeline.create(event, checked=True))

    walk = replay_all(timeline, base, ctx)

    assert walk.session.request("R2") == located
    assert timeline.entries[0].event == event


def test_known_ids_come_from_the_base_day_and_earlier_urgent_entries():
    base = new_session(context())
    timeline = Timeline()
    urgent = Event(type=EventType.URGENT, time="13:00", request=req("URG-1", 0, 0, "13:00", "15:00"))
    timeline.insert(timeline.create(urgent))

    assert set(known_requests(base, timeline.entries)) == {"R1", "R2", "R3", "URG-1"}
    check_known(base, timeline.entries, cancel("URG-1", "13:00"))
    check_known(base, timeline.entries, cancel("R1", "09:00"))
    cases = [
        (cancel("URG-1", "12:59"), "Заявка URG-1 не найдена."),
        (cancel("NOPE", "13:00"), "Заявка NOPE не найдена."),
        (Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id="E9"), "Инженер E9 не найден."),
        (
            Event(type=EventType.ENGINEER_DELAYED, time="13:00", engineer_id="E9", delay_min=30),
            "Инженер E9 не найден.",
        ),
        (urgent.model_copy(update={"time": "15:00"}), "Заявка с номером URG-1 уже есть в плане."),
        (
            Event(type=EventType.URGENT, time="09:00", request=req("R1", 0, 0, "13:00", "15:00")),
            "Заявка с номером R1 уже есть в плане.",
        ),
    ]
    for event, text in cases:
        with pytest.raises(EventRejected) as error:
            check_known(base, timeline.entries, event)
        assert str(error.value) == text


def unavailable(engineer_id, time):
    return Event(type=EventType.ENGINEER_UNAVAILABLE, time=time, engineer_id=engineer_id)


def _variants_of(timeline, base, ctx, entry):
    """Как фоновый расчёт: три шага события без выбора после посчитанного прохода."""
    walk = timeline.walk(base)
    for variant in VARIANTS:
        timeline.store(walk, entry, replay_step(walk.session, entry, ctx, 99, variant), variant)
    return walk


def test_walk_stops_at_a_breaking_event_without_a_choice_until_its_variants_are_known(solves):
    ctx = context()
    base = new_session(ctx)
    busy = busy_engineer(base.plan)
    breaking, later = _added(timeline := Timeline(), unavailable(busy, "13:00"), cancel("R1", "16:00"))

    assert entry_token(breaking) == breaking.id and entry_token(breaking, "keep") == f"{breaking.id}@keep"
    walk = timeline.walk(base)
    assert (walk.done, walk.awaiting) == (0, None)

    _variants_of(timeline, base, ctx, breaking)
    walk = timeline.walk(base)
    assert (walk.done, walk.awaiting) == (0, breaking)
    assert timeline.pending_choice(base, 12 * 60) is None
    pending = timeline.pending_choice(base, 15 * 60)
    assert pending is not None
    pending_walk, entry = pending
    assert entry == breaking and pending_walk.session == base
    items, ready = timeline.view(base, 15 * 60)
    assert [item.status for item in items] == ["awaiting", "pending"] and ready is True
    items, _ = timeline.view(base, 12 * 60)
    assert [item.status for item in items] == ["pending", "pending"]


def test_chosen_variant_is_applied_and_changing_it_replays_later_events_with_their_choices(solves):
    ctx = context()
    base = new_session(ctx)
    busy = busy_engineer(base.plan)
    timeline = Timeline()
    breaking, later = _added(timeline, unavailable(busy, "13:00"), cancel("R1", "16:00"))
    _variants_of(timeline, base, ctx, breaking)

    chosen = timeline.set_variant(breaking.id, "keep")
    assert chosen.variant == "keep" and timeline.find(breaking.id) is chosen
    walk = replay_all(timeline, base, ctx)
    assert walk.awaiting is None and walk.done == 2
    assert walk.keys[0] == ((), f"{breaking.id}@keep")
    assert walk.keys[1] == ((f"{breaking.id}@keep",), later.id)
    kept_plan = walk.steps[0].session.plan

    timeline.set_variant(breaking.id, "optimal")
    walk = replay_all(timeline, base, ctx)
    assert walk.keys[1] == ((f"{breaking.id}@optimal",), later.id)
    assert walk.steps[0].session.plan != kept_plan
    assert timeline.set_variant("tl_404", "keep") is None


def test_rejected_breaking_event_needs_no_choice(solves):
    ctx = context()
    base = new_session(ctx)
    timeline = Timeline()
    (ghost,) = _added(timeline, unavailable("E404", "13:00"))
    walk = timeline.walk(base)
    timeline.store(walk, ghost, replay_step(walk.session, ghost, ctx, 2, "optimal"), "optimal")
    walk = timeline.walk(base)
    assert walk.awaiting is None and walk.done == 1 and walk.steps[0].reason is not None
    items, ready = timeline.view(base, 15 * 60)
    assert [item.status for item in items] == ["rejected"] and ready is True


def test_prune_keeps_the_other_variants_of_chosen_events_and_remove_drops_them(solves):
    ctx = context()
    base = new_session(ctx)
    busy = busy_engineer(base.plan)
    timeline = Timeline()
    (breaking,) = _added(timeline, unavailable(busy, "13:00"))
    _variants_of(timeline, base, ctx, breaking)
    timeline.set_variant(breaking.id, "stable")
    walk = replay_all(timeline, base, ctx)

    timeline.prune(walk)
    assert {key[1] for key in timeline.steps} == {f"{breaking.id}@{variant}" for variant in VARIANTS}
    timeline.remove(breaking.id)
    assert timeline.steps == {}


def test_prune_keeps_the_counted_give_it_to_a_brigade_step(solves):
    """Шаг «отдать бригаде» считают по запросу диспетчера: полный проход его не выбрасывает.

    Иначе тот же выбор бригады после любого обращения к состоянию считался бы решателем заново.
    """
    ctx = context()
    base = new_session(ctx)
    timeline = Timeline()
    (entry,) = _added(
        timeline, Event(type=EventType.URGENT, time="12:00", request=req("U1", 0, 0, "13:00", "17:00"))
    )
    walk = _variants_of(timeline, base, ctx, entry)
    given = assign_variant("E2")
    timeline.store(walk, entry, replay_step(walk.session, entry, ctx, 99, given), given)
    timeline.set_variant(entry.id, "optimal")
    walk = replay_all(timeline, base, ctx)

    timeline.prune(walk)

    assert {key[1] for key in timeline.steps} == {f"{entry.id}@{variant}" for variant in (*VARIANTS, given)}


def test_entry_can_be_created_with_a_variant():
    entry = Timeline().create(unavailable("E1", "13:00"), variant="optimal")
    assert entry.variant == "optimal" and entry_token(entry) == "tl_1@optimal"


def test_a_step_counted_again_keeps_the_plan_the_dispatcher_saw(solves):
    """Кэш шагов в памяти живёт тем же правилом, что и база: выигрывает первый писатель.

    Солвер ограничен по времени и недетерминирован, повтор того же ключа нашёл бы другие маршруты. Если
    память оставляла бы последний план, а база (ON CONFLICT DO NOTHING) — первый, то перезапуск показал
    бы диспетчеру не тот план, что минуту назад.
    """
    timeline = Timeline()
    session = new_session()
    [entry] = _added(timeline, cancel("R2", "09:00"))
    walk = timeline.walk(session)
    shown = replay_step(session, entry, context(), 2)
    timeline.store(walk, entry, shown)

    timeline.store(walk, entry, replay_step(session, entry, context(), 3))

    assert timeline.steps[(walk.prefix, entry_token(entry))] is shown
