"""Сессия в jsonb и обратно: после перезапуска диспетчер должен увидеть ТОТ ЖЕ план."""

import json

from app.api.registry import PreparedDay
from app.planning.session import apply_event
from app.state.codec import dump_prepared, dump_session, load_prepared, load_session
from tests.helpers import req
from tests.planning_helpers import OFFICE, context, day_engineers, day_requests, new_session
from tests.timeline_helpers import cancel


def prepared_day(control=None):
    return PreparedDay("t", "Тест", OFFICE, day_requests(), day_engineers(), control, False)


def roundtrip(session, day=None):
    day = day or prepared_day()
    return load_session(dump_session(session), dataset_id="d_test", prepared=day, ctx=context())


def test_plan_comes_back_byte_for_byte():
    """Главное свойство: план, записанный в базу, читается ровно тем же — солвер второй раз не запускается."""
    session = new_session()
    restored = roundtrip(session)
    assert restored.plan.model_dump_json() == session.plan.model_dump_json()
    assert restored.baseline.model_dump_json() == session.baseline.model_dump_json()


def test_session_keeps_everything_the_screen_shows():
    session = new_session()
    after = apply_event(session, cancel("R2", "09:00"), context(), version=7)
    restored = roundtrip(after)
    for field in ("plan", "baseline", "previous_plan", "last_diff"):
        assert getattr(restored, field).model_dump() == getattr(after, field).model_dump(), field
    assert [item.model_dump() for item in restored.events] == [item.model_dump() for item in after.events]
    assert [r.model_dump() for r in restored.requests] == [r.model_dump() for r in after.requests]
    assert [e.model_dump() for e in restored.engineers] == [e.model_dump() for e in after.engineers]
    assert (restored.now, restored.version) == (after.now, after.version)
    assert (restored.workload_level, restored.lunch_enabled) == (
        after.workload_level,
        after.lunch_enabled,
    )
    assert restored.precomputed == after.precomputed


def test_problem_state_of_the_day_survives():
    """Закреплённая работа, доступность бригад и прежние назначения — то, что копят события дня."""
    session = new_session()
    after = apply_event(session, cancel("R3", "11:00"), context())
    restored = roundtrip(after)
    before, now = after.problem, restored.problem
    assert now.open_request_ids == before.open_request_ids
    assert now.previous_assignment == before.previous_assignment
    assert now.previous_order == before.previous_order
    assert now.previous_start == before.previous_start
    assert now.now == before.now
    assert now.lunch == before.lunch
    assert {rid: [v.model_dump() for v in visits] for rid, visits in now.pinned.items()} == {
        rid: [v.model_dump() for v in visits] for rid, visits in before.pinned.items()
    }
    assert [
        (s.engineer.id, s.start_node, s.available_from, s.available_until, s.equipment_left)
        for s in now.states
    ] == [
        (s.engineer.id, s.start_node, s.available_from, s.available_until, s.equipment_left)
        for s in before.states
    ]


def test_matrix_is_rebuilt_not_stored():
    """Матрица дороги в снимок не попадает: 278 КБ на шаг, а собрать её заново можно из тех же точек."""
    session = new_session()
    payload = json.loads(dump_session(session))
    assert "travel" not in payload["problem"]
    assert payload["matrix_source"] == session.problem.travel.base.source
    restored = roundtrip(session)
    engineer = restored.engineers[0]
    assert restored.problem.travel_min(0, 1, engineer) == session.problem.travel_min(0, 1, engineer)


def test_day_inputs_come_back():
    day = prepared_day(control=new_session().plan)
    restored = load_prepared(dump_prepared(day))
    assert (restored.region, restored.region_title, restored.generated) == ("t", "Тест", False)
    assert restored.office.model_dump() == OFFICE.model_dump()
    assert [r.id for r in restored.requests] == [r.id for r in day.requests]
    assert [e.id for e in restored.engineers] == [e.id for e in day.engineers]
    assert restored.control.model_dump() == day.control.model_dump()


def test_request_without_a_point_stays_unplannable():
    """Заявка без координат в задачу не попадает, а её причина — попадает: после подъёма всё так же."""
    requests = [*day_requests(), req("R9", 0, 0, "10:00", "12:00").model_copy(update={"lat": None})]
    session = new_session(requests=requests)
    day = PreparedDay("t", "Тест", OFFICE, requests, day_engineers(), None, False)
    restored = roundtrip(session, day)
    assert [r.id for r in restored.problem.requests] == [r.id for r in session.problem.requests]
    assert [u.model_dump() for u in restored.problem.unplannable] == [
        u.model_dump() for u in session.problem.unplannable
    ]


def test_a_zero_byte_does_not_reach_the_snapshot():
    """jsonb нулевого байта не принимает, и день с ним не сохранился бы целиком: кодек убирает его сам."""
    requests = [day_requests()[0].model_copy(update={"address": "ул.\x00 Тихая"}), *day_requests()[1:]]
    day = PreparedDay("t", "Тест", OFFICE, requests, day_engineers(), None, False)

    raw = dump_prepared(day)

    # Искать нужно запись байта в JSON, а не сам байт: model_dump_json отдаёт его escape-последовательностью.
    assert "\\u0000" not in raw
    assert load_prepared(raw).requests[0].address == "ул. Тихая"


def test_text_that_only_looks_like_an_escape_is_left_alone():
    """Обратная косая, буква u и четыре нуля в адресе — обычные символы: портить их чисткой нельзя."""
    address = "ул. \\u0000 Тихая"
    requests = [day_requests()[0].model_copy(update={"address": address}), *day_requests()[1:]]
    day = PreparedDay("t", "Тест", OFFICE, requests, day_engineers(), None, False)

    assert load_prepared(dump_prepared(day)).requests[0].address == address
