"""Переназначение заявки диспетчером на бригаду посреди дня: закрепление, отказы и варианты."""

from dataclasses import replace

import pytest

from app.domain.enums import EventType, Priority, ReasonCode, Skill, Transport
from app.domain.models import Event
from app.planning.session import EventRejected, apply_event, check_event
from app.planning.variants import VARIANTS
from tests.helpers import eng, req
from tests.planning_helpers import (
    EXACT_TRAVEL_LEVEL,
    IN_TRANSIT_TO_B,
    busy_engineer,
    context,
    day_requests,
    new_session,
    other_engineer,
    routes,
    transit_session,
    visit_times,
)
from tests.timeline_helpers import cancel, fcfs_solves


@pytest.fixture
def solves(monkeypatch):
    return fcfs_solves(monkeypatch)


def reassign(request_id, engineer_id, time="12:00", **extra):
    return Event(
        type=EventType.REQUEST_REASSIGNED, time=time, request_id=request_id, engineer_id=engineer_id, **extra
    )


def owner(plan, request_id):
    return next((engineer_id for engineer_id, visits in routes(plan).items() if request_id in visits), None)


def test_event_needs_a_request_and_an_engineer():
    with pytest.raises(ValueError, match="для переназначения заявки нужны request_id и engineer_id"):
        Event(type=EventType.REQUEST_REASSIGNED, time="12:00", request_id="R1")
    with pytest.raises(ValueError, match="для переназначения заявки нужны request_id и engineer_id"):
        Event(type=EventType.REQUEST_REASSIGNED, time="12:00", engineer_id="E1")


@pytest.mark.parametrize("variant", VARIANTS)
def test_request_lands_on_the_chosen_brigade_with_every_variant(solves, variant):
    ctx = context()
    base = new_session(ctx)
    assert routes(base.plan) == {"E1": ["R1", "R2", "R3"], "E2": []}

    updated = apply_event(base, reassign("R3", "E2", previous_engineer_id="E9"), ctx, variant=variant)

    assert routes(updated.plan) == {"E1": ["R1", "R2"], "E2": ["R3"]}
    assert updated.request("R3").fixed_engineer_id == "E2"
    assert base.request("R3").fixed_engineer_id is None  # исходная сессия не изменилась
    [applied] = updated.events
    # previous_engineer_id от клиента не используется: его заполняет backend по плану до события.
    assert (applied.event.previous_engineer_id, applied.event.engineer_id) == ("E1", "E2")
    assert [
        (move.request_id, move.from_engineer_id, move.to_engineer_id) for move in updated.last_diff.moved
    ] == [("R3", "E1", "E2")]


@pytest.mark.parametrize("variant", VARIANTS)
def test_request_the_brigade_is_already_driving_to_moves_with_every_variant(solves, variant):
    """В 09:47 бригада уже едет к B: удержание визита в пути для переназначенной заявки снимается."""
    ctx = context()
    base = transit_session(ctx)
    busy = busy_engineer(base.plan)
    other = other_engineer(busy)
    assert routes(base.plan) == {busy: ["A", "B", "C"], other: []}

    updated = apply_event(base, reassign("B", other, time=IN_TRANSIT_TO_B), ctx, variant=variant)

    assert owner(updated.plan, "B") == other
    assert "B" not in routes(updated.plan)[busy]
    assert updated.events[-1].event.previous_engineer_id == busy


def test_check_event_fills_the_previous_brigade_without_changing_the_session(solves):
    ctx = context()
    base = new_session(ctx)
    stored = check_event(base, reassign("R3", "E2"), ctx)
    assert (stored.previous_engineer_id, stored.request_id, stored.engineer_id) == ("E1", "R3", "E2")
    assert base.request("R3").fixed_engineer_id is None


def test_request_without_an_engineer_has_no_previous_brigade(solves):
    ctx = context()
    base = new_session(ctx)
    # «Ничего не менять» на недоступность E1 оставляет R2 и R3 без инженера.
    kept = apply_event(
        base, Event(type=EventType.ENGINEER_UNAVAILABLE, time="11:00", engineer_id="E1"), ctx, variant="keep"
    )
    assert owner(kept.plan, "R2") is None

    updated = apply_event(kept, reassign("R2", "E2", time="11:30"), ctx)

    assert owner(updated.plan, "R2") == "E2"
    assert updated.events[-1].event.previous_engineer_id is None


def far_request():
    """До заявки 40 км, а окно 09:00–09:30: не успевает никто."""
    return req("F", 40, 0, "09:00", "09:30")


def no_point_request():
    return req("N", 0, 0, "10:00", "17:00").model_copy(update={"lat": None, "lon": None})


def rejection_cases(ctx):
    day = new_session(ctx)
    cancelled = apply_event(day, cancel("R2", "09:00"), ctx)
    with_far = new_session(ctx, requests=[*day_requests(), far_request()])
    return [
        (day, reassign("R9", "E2"), "Заявка R9 не найдена."),
        (cancelled, reassign("R2", "E2", time="09:30"), "Заявка R2 отменена, назначить её нельзя."),
        (day, reassign("R1", "E2", time="11:00"), "Заявка R1 уже в работе с 10:00, переназначить её нельзя."),
        (day, reassign("R3", "E9"), "Инженер E9 не найден."),
        (
            new_session(ctx, engineers=[eng("E1"), eng("E2", available=False)]),
            reassign("R3", "E2"),
            "Инженер E2 недоступен с 09:00, назначить заявку нельзя.",
        ),
        (
            new_session(ctx, engineers=[eng("E1"), eng("E2", skills=[Skill.CONNECTION])]),
            reassign("R3", "E2"),
            "У Инженер E2 нет навыка «Локальные работы».",
        ),
        (
            new_session(
                ctx,
                requests=[*day_requests()[:2], req("R3", -1, 0, "15:00", "17:00", transport=Transport.CAR)],
                engineers=[eng("E1"), eng("E2", transport=Transport.BIKE)],
            ),
            reassign("R3", "E2"),
            "Заявке R3 нужен транспорт «Автомобиль», у Инженер E2 «Велосипед».",
        ),
        (day, reassign("R3", "E1"), "Заявка R3 уже у Инженер E1."),
        (
            new_session(ctx, requests=[*day_requests(), no_point_request()]),
            reassign("N", "E2"),
            "У заявки N нет точки на карте, назначить её нельзя.",
        ),
        (with_far, reassign("F", "E2", time="18:10"), "У Инженер E2 не осталось рабочего времени сегодня."),
        (
            with_far,
            reassign("F", "E2", time="09:00"),
            "Инженер E2 не успевает к заявке F даже без других заявок: начнёт не раньше 11:19, "
            "окно 09:00–09:30, смена до 18:00.",
        ),
    ]


def test_rejections_do_not_depend_on_the_variant(solves):
    ctx = context()
    for session, event, text in rejection_cases(ctx):
        for variant in VARIANTS:
            with pytest.raises(EventRejected) as error:
                apply_event(session, event, ctx, variant=variant)
            assert str(error.value) == text, (event.request_id, event.engineer_id, variant)


def test_pin_is_released_when_the_brigade_becomes_unavailable(solves):
    ctx = context()
    pinned = apply_event(new_session(ctx), reassign("R3", "E2"), ctx)

    updated = apply_event(
        pinned, Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id="E2"), ctx
    )

    assert updated.request("R3").fixed_engineer_id is None
    assert owner(updated.plan, "R3") == "E1"


def test_pin_is_released_when_the_brigade_transport_no_longer_fits(solves):
    ctx = context()
    requests = [*day_requests()[:2], req("R3", -1, 0, "15:00", "17:00", transport=Transport.CAR)]
    pinned = apply_event(new_session(ctx, requests=requests), reassign("R3", "E2"), ctx)
    assert pinned.request("R3").fixed_engineer_id == "E2"

    changed = Event(
        type=EventType.ENGINEER_TRANSPORT_CHANGED, time="13:00", engineer_id="E2", transport=Transport.BIKE
    )
    updated = apply_event(pinned, changed, ctx)

    assert updated.request("R3").fixed_engineer_id is None
    assert owner(updated.plan, "R3") == "E1"


def test_pin_is_released_when_the_request_no_longer_fits_the_brigade(solves):
    ctx = context()
    engineers = [eng("E1"), eng("E2", skills=[Skill.LOCAL])]
    pinned = apply_event(new_session(ctx, engineers=engineers), reassign("R3", "E2"), ctx)
    sent = pinned.request("R3").model_copy(update={"skill": Skill.CONNECTION, "fixed_engineer_id": None})

    updated = apply_event(
        pinned, Event(type=EventType.REQUEST_UPDATED, time="13:00", request_id="R3", request=sent), ctx
    )

    assert updated.request("R3").fixed_engineer_id is None
    assert owner(updated.plan, "R3") == "E1"


def test_request_update_keeps_the_pin_when_the_brigade_still_fits(solves):
    ctx = context()
    pinned = apply_event(new_session(ctx), reassign("R3", "E2"), ctx)
    # Клиент прислал заявку без закрепления: поле не редактируется, закрепление остаётся.
    sent = pinned.request("R3").model_copy(update={"duration_min": 45, "fixed_engineer_id": None})

    updated = apply_event(
        pinned, Event(type=EventType.REQUEST_UPDATED, time="13:00", request_id="R3", request=sent), ctx
    )

    assert updated.request("R3").fixed_engineer_id == "E2"
    assert owner(updated.plan, "R3") == "E2"


def test_urgent_request_cannot_arrive_pinned(solves):
    ctx = context()
    urgent = req("U1", 0.5, 0.5, "13:00", "15:00").model_copy(update={"fixed_engineer_id": "E2"})
    updated = apply_event(new_session(ctx), Event(type=EventType.URGENT, time="12:00", request=urgent), ctx)
    assert updated.request("U1").fixed_engineer_id is None


def test_pin_persists_through_later_events_with_or_tools():
    """Настоящий OR-Tools: без закрепления решатель вернул бы заявку к бригаде с работой, это на бригаду дешевле."""
    ctx = context()
    base = new_session(ctx)
    busy = busy_engineer(base.plan)
    assert routes(base.plan)[busy] == ["R1", "R2", "R3"]
    other = other_engineer(busy)

    pinned = apply_event(base, reassign("R3", other), ctx)
    assert routes(pinned.plan) == {busy: ["R1", "R2"], other: ["R3"]}

    later = apply_event(pinned, cancel("R2", "13:00"), ctx)

    assert later.request("R3").fixed_engineer_id == other
    assert (routes(later.plan), later.plan.unassigned) == ({busy: ["R1"], other: ["R3"]}, [])
    unpinned = replace(
        pinned,
        requests=[request.model_copy(update={"fixed_engineer_id": None}) for request in pinned.requests],
    )
    assert routes(apply_event(unpinned, cancel("R2", "13:00"), ctx).plan) == {busy: ["R1", "R3"], other: []}


def test_or_tools_drops_even_an_urgent_request_to_keep_the_pinned_one():
    """Настоящий OR-Tools. Подключение A умеет только E1, B — любая бригада; вместе в одно время не успеть."""
    ctx = context()
    requests = [
        req("A", 1, 0, "10:00", "10:30", duration=120, skill=Skill.CONNECTION, priority=Priority.URGENT),
        req("B", 1, 0, "10:00", "10:30", duration=120),
    ]
    engineers = [eng("E1"), eng("E2", skills=[Skill.LOCAL])]
    base = new_session(ctx, requests=requests, engineers=engineers)
    assert routes(base.plan) == {"E1": ["A"], "E2": ["B"]}

    for variant in VARIANTS:
        updated = apply_event(base, reassign("B", "E1", time="09:00"), ctx, variant=variant)

        assert routes(updated.plan) == {"E1": ["B"], "E2": []}, variant
        [dropped] = updated.plan.unassigned
        assert dropped.request_id == "A"
        if variant == "keep":
            assert dropped.reason_code == ReasonCode.NO_FREE_ENGINEER
            assert dropped.reason_text == (
                "Вариант «Вставить в маршрут»: Инженер E1 пропускает заявку, чтобы успеть к заявке B."
            )


def insert_day(ctx):
    """P1, P2, P3 и X в одной точке в 1 км от офиса, дорога без запаса и без обеда: минуты считаются вручную.

    FCFS: E1 — P1 09:04–10:04, P2 10:04–11:04, P3 11:04–12:04 и Q в 2 км от них. X в конец маршрута E1 уже
    не помещается и уходит E2, S не помещается в конец маршрутов E1 и E2 и уходит E3.
    """
    requests = [
        req("P1", 1, 0, "09:00", "10:00", duration=60),
        req("P2", 1, 0, "10:00", "11:00", duration=60),
        req("P3", 1, 0, "11:00", "13:00", duration=60),
        req("X", 1, 0, "10:00", "10:30", duration=60),
        req("Q", -1, 0, "09:00", "17:00"),
        req("S", 2, 0, "09:00", "10:00"),
    ]
    return new_session(
        ctx,
        requests=requests,
        engineers=[eng("E1"), eng("E2"), eng("E3")],
        workload_level=EXACT_TRAVEL_LEVEL,
        lunch_enabled=False,
    )


def test_insert_skips_only_what_does_not_fit_and_returns_to_the_previous_order(solves):
    ctx = context()
    base = insert_day(ctx)
    assert routes(base.plan) == {"E1": ["P1", "P2", "P3", "Q"], "E2": ["X"], "E3": ["S"]}
    solves.clear()

    # В 09:30 P1 уже идёт до 10:04. X с 10:04 до 11:04: к P2 (до 11:00) E1 опаздывает, а P3 и Q успевает.
    inserted = apply_event(base, reassign("X", "E1", time="09:30"), ctx, variant="keep")

    assert routes(inserted.plan) == {"E1": ["P1", "X", "P3", "Q"], "E2": [], "E3": ["S"]}
    assert inserted.plan.violations == []
    assert [(item.request_id, item.reason_code, item.reason_text) for item in inserted.plan.unassigned] == [
        (
            "P2",
            ReasonCode.NO_FREE_ENGINEER,
            "Вариант «Вставить в маршрут»: Инженер E1 пропускает заявку, чтобы успеть к заявке X.",
        )
    ]
    visits = {visit.request_id: visit for visit in inserted.plan.routes[0].visits}
    assert [(rid, visits[rid].start) for rid in ("X", "P3")] == [("X", 604), ("P3", 664)]
    # Маршрут E3 не пересчитывался: визиты в то же время.
    assert visit_times(inserted.plan, "E3") == visit_times(base.plan, "E3")
    # Без решателя: FCFS в тестах подменяет _solve, и вставка его не вызывает.
    assert solves == []


def test_insert_signs_an_urgent_request_in_the_reason(solves):
    """Причина пропущенной заявки лежит в карточке под подписью заявки: срочная названа в ней «URG-<номер>»."""
    ctx = context()
    base = insert_day(ctx)
    urgent = replace(
        base,
        requests=[
            request.model_copy(update={"priority": Priority.URGENT}) if request.id == "X" else request
            for request in base.requests
        ],
    )

    inserted = apply_event(urgent, reassign("X", "E1", time="09:30"), ctx, variant="keep")

    assert [(item.request_id, item.reason_text) for item in inserted.plan.unassigned] == [
        (
            "P2",
            "Вариант «Вставить в маршрут»: Инженер E1 пропускает заявку, чтобы успеть к заявке URG-X.",
        )
    ]


@pytest.mark.parametrize(
    ("first", "expected", "skipped"),
    [
        ({"priority": Priority.URGENT}, ["U", "X"], ["N1", "N2"]),
        ({"fixed_engineer_id": "E1"}, ["U", "X"], ["N1", "N2"]),
        ({}, ["X", "N1", "N2"], ["U"]),
    ],
)
def test_insert_prefers_skipping_normal_requests_then_fewer_requests(solves, first, expected, skipped):
    """Вставка в начало пропускает первую заявку U, вставка после U — две обычные N1 и N2.

    Срочную или закреплённую за бригадой U бригада не пропускает, обычную — пропускает: так пропущена одна заявка.
    """
    ctx = context()
    requests = [
        req("U", 1, 0, "09:00", "09:30", duration=60).model_copy(update=first),
        req("N1", 1, 0, "10:00", "10:30"),
        req("N2", 1, 0, "10:30", "11:00"),
        req("X", 1, 0, "09:00", "10:30", duration=60),
    ]
    base = new_session(ctx, requests=requests, workload_level=EXACT_TRAVEL_LEVEL, lunch_enabled=False)
    assert routes(base.plan) == {"E1": ["U", "N1", "N2"], "E2": ["X"]}

    inserted = apply_event(base, reassign("X", "E1", time="09:00"), ctx, variant="keep")

    assert routes(inserted.plan) == {"E1": expected, "E2": []}
    assert [item.request_id for item in inserted.plan.unassigned] == skipped


@pytest.mark.parametrize("important", [{"priority": Priority.URGENT}, {"fixed_engineer_id": "E1"}])
def test_insert_skips_a_normal_request_rather_than_an_urgent_or_pinned_one_later_in_the_route(
    solves, important
):
    """X с 09:04 до 10:04. После X бригада успевает к B (до 10:30) или к U (с 10:30), но не к обеим.

    Место вставки одно и то же, а пропустить можно разные заявки: срочную или закреплённую U бригада не пропускает,
    хотя U в маршруте после обычной B.
    """
    ctx = context()
    requests = [
        req("B", 1, 0, "09:00", "10:30", duration=60),
        req("U", 1, 0, "10:30", "11:00").model_copy(update=important),
        req("X", 1, 0, "09:00", "09:30", duration=60),
    ]
    base = new_session(ctx, requests=requests, workload_level=EXACT_TRAVEL_LEVEL, lunch_enabled=False)
    assert routes(base.plan) == {"E1": ["B", "U"], "E2": ["X"]}

    inserted = apply_event(base, reassign("X", "E1", time="09:00"), ctx, variant="keep")

    assert routes(inserted.plan) == {"E1": ["X", "U"], "E2": []}
    assert inserted.plan.violations == []
    assert [(item.request_id, item.reason_text) for item in inserted.plan.unassigned] == [
        ("B", "Вариант «Вставить в маршрут»: Инженер E1 пропускает заявку, чтобы успеть к заявке X.")
    ]


@pytest.mark.parametrize("important", [{"priority": Priority.URGENT}, {"fixed_engineer_id": "E1"}])
def test_insert_takes_urgent_and_pinned_requests_first_at_the_same_place(solves, important):
    """B 09:04–10:04, U 10:00–11:30, X с 11:00 до 11:30. После B или после U бригада успевает к X, после обеих — нет.

    При вставке X в конец бригада сначала берёт срочную или закреплённую U и пропускает обычную B, хотя B в маршруте
    раньше U.
    """
    ctx = context()
    requests = [
        req("B", 1, 0, "09:00", "09:30", duration=60),
        req("U", 1, 0, "10:00", "10:30", duration=90).model_copy(update=important),
        req("X", 1, 0, "11:00", "11:30", duration=60),
    ]
    base = new_session(ctx, requests=requests, workload_level=EXACT_TRAVEL_LEVEL, lunch_enabled=False)
    assert routes(base.plan) == {"E1": ["B", "U"], "E2": ["X"]}

    inserted = apply_event(base, reassign("X", "E1", time="09:00"), ctx, variant="keep")

    assert routes(inserted.plan) == {"E1": ["U", "X"], "E2": []}
    assert inserted.plan.violations == []
    assert [item.request_id for item in inserted.plan.unassigned] == ["B"]


def kept_before_insert(ctx, requests, event, lunch_enabled=False):
    """Сессия после события event с вариантом «Ничего не менять»: X у E2, у E1 маршрут с нарушениями.

    Если FCFS отдал X бригаде E1, в 09:00 X переназначается на E2. Дальше тест вставкой возвращает X к E1.
    """
    base = new_session(ctx, requests=requests, workload_level=EXACT_TRAVEL_LEVEL, lunch_enabled=lunch_enabled)
    moved = (
        apply_event(base, reassign("X", "E2", time="09:00"), ctx) if owner(base.plan, "X") == "E1" else base
    )
    return apply_event(moved, event, ctx, variant="keep")


def e1_delayed():
    return Event(type=EventType.ENGINEER_DELAYED, time="09:30", engineer_id="E1", delay_min=60)


def late_minutes(plan):
    return {
        visit.request_id: visit.late_min for route in plan.routes for visit in route.visits if visit.late_min
    }


@pytest.mark.parametrize(
    ("x_window", "expected", "skipped", "late"),
    [
        # X в конце маршрута P2 не задевает: опоздание, которое было до события, бригаду не заставляет пропускать.
        (("15:00", "17:00"), ["P1", "P2", "P3", "X"], [], {"P2": 34}),
        # X 11:04–11:34 перед P2 увеличил бы её опоздание: P2 бригада пропускает, P3 успевает.
        (("11:00", "11:30"), ["P1", "X", "P3"], ["P2"], {}),
    ],
)
def test_insert_keeps_a_visit_late_since_before_the_event_unless_it_gets_later(
    solves, x_window, expected, skipped, late
):
    """P1, P2, P3 и X в одной точке. E1 задержан в 09:30 на 60 минут: P1 идёт 09:04–11:04, P2 (окно до 10:30)
    начинается в 11:04 с опозданием 34 минуты, P3 12:04–13:04."""
    ctx = context()
    requests = [
        req("P1", 1, 0, "09:00", "10:00", duration=60),
        req("P2", 1, 0, "10:00", "10:30", duration=60),
        req("P3", 1, 0, "11:00", "13:00", duration=60),
        req("X", 1, 0, *x_window),
    ]
    kept = kept_before_insert(ctx, requests, e1_delayed())
    assert (routes(kept.plan), late_minutes(kept.plan)) == (
        {"E1": ["P1", "P2", "P3"], "E2": ["X"]},
        {"P2": 34},
    )

    inserted = apply_event(kept, reassign("X", "E1", time="10:00"), ctx, variant="keep")

    assert routes(inserted.plan) == {"E1": expected, "E2": []}
    assert [item.request_id for item in inserted.plan.unassigned] == skipped
    assert late_minutes(inserted.plan) == late


def test_insert_keeps_a_visit_past_the_shift_since_before_the_event(solves):
    """На велосипеде дорога к P3 на 8 минут дольше, и P3 заканчивается в 18:05. X до P2 время P3 не меняет."""
    ctx = context()
    requests = [
        req("P2", 1, 0, "10:00", "10:30"),
        req("P3", 11, 0, "10:30", "17:00", duration=415),
        req("X", 1, 0, "09:00", "09:40", duration=20),
    ]
    bike = Event(
        type=EventType.ENGINEER_TRANSPORT_CHANGED, time="09:00", engineer_id="E1", transport=Transport.BIKE
    )
    kept = kept_before_insert(ctx, requests, bike)
    assert routes(kept.plan) == {"E1": ["P2", "P3"], "E2": ["X"]}
    assert kept.plan.violations == ["P3: окончание 18:05 позже конца смены 18:00"]

    inserted = apply_event(kept, reassign("X", "E1", time="09:00"), ctx, variant="keep")

    assert (routes(inserted.plan), inserted.plan.unassigned) == ({"E1": ["X", "P2", "P3"], "E2": []}, [])
    assert inserted.plan.violations == kept.plan.violations


def test_insert_keeps_the_route_where_lunch_did_not_fit_since_before_the_event(solves):
    """E1 задержан в 09:30 на 60 минут: без обеда P2 11:04–14:04 и P3 с 14:04 (окно до 14:30) успевают, а обеду места
    нет. План ставит обед в 12:00, и P2 с P3 опаздывают.

    X 17:50–18:00 после P3 ничего не сдвигает: бригада берёт X, ничего не пропуская.
    """
    ctx = context()
    requests = [
        req("P1", 1, 0, "09:00", "10:00", duration=60),
        req("P2", 1, 0, "10:00", "12:00", duration=180),
        req("P3", 1, 0, "14:00", "14:30", duration=120),
        req("X", 1, 0, "17:50", "18:00", duration=10),
    ]
    kept = kept_before_insert(ctx, requests, e1_delayed(), lunch_enabled=True)
    assert routes(kept.plan) == {"E1": ["P1", "P2", "P3"], "E2": ["X"]}
    assert late_minutes(kept.plan) == {"P2": 45, "P3": 75}
    assert "у Инженер E1 не помещается обед 45 мин с началом 12:00–15:00" in kept.plan.violations

    inserted = apply_event(kept, reassign("X", "E1", time="10:00"), ctx, variant="keep")

    assert (routes(inserted.plan), inserted.plan.unassigned) == (
        {"E1": ["P1", "P2", "P3", "X"], "E2": []},
        [],
    )
    assert inserted.plan.violations == kept.plan.violations


def test_insert_does_not_take_the_lunch_break_of_a_route_late_since_before_the_event(solves):
    """E1 задержан в 09:30 на 60 минут: P2 11:04–12:04 с опозданием 34 минуты, обед 12:04–12:49, P3 13:30–15:30.

    X 12:10–13:10 занимает место обеда, а после P3 обед уже не начать. Без обеда маршрут прошёл бы по опозданиям, но
    бригада без обеда не остаётся: она пропускает P2, обедает после X и успевает к P3.
    """
    ctx = context()
    requests = [
        req("P1", 1, 0, "09:00", "10:00", duration=60),
        req("P2", 1, 0, "10:00", "10:30", duration=60),
        req("P3", 1, 0, "13:30", "14:00", duration=120),
        req("X", 1, 0, "12:10", "12:40", duration=60),
    ]
    kept = kept_before_insert(ctx, requests, e1_delayed(), lunch_enabled=True)
    assert (routes(kept.plan), late_minutes(kept.plan)) == (
        {"E1": ["P1", "P2", "P3"], "E2": ["X"]},
        {"P2": 34},
    )

    inserted = apply_event(kept, reassign("X", "E1", time="10:00"), ctx, variant="keep")

    assert routes(inserted.plan) == {"E1": ["P1", "X", "P3"], "E2": []}
    assert inserted.plan.violations == []
    assert [item.request_id for item in inserted.plan.unassigned] == ["P2"]
    assert inserted.plan.routes[0].lunch.start == 790  # 13:10, сразу после X
