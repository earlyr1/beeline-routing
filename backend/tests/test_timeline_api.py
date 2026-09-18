"""Время дня и события на шкале через API: текущее время плана, таймлайн и фоновый предподсчёт."""

import json
import threading

import pytest

from app.domain.enums import EventType
from app.domain.models import Event
from tests.api_helpers import HashGeocoder, make_client, sample_bundle, upload
from tests.helpers import req
from tests.llm_helpers import ScriptedProvider, completion, tool_call
from tests.timeline_helpers import cancel, fcfs_solves, restore

TIMELINE_FIELDS = ("cursor", "timeline", "timeline_ready")


@pytest.fixture
def solves(monkeypatch):
    return fcfs_solves(monkeypatch)


class Background(list):
    """Фоновые задачи не запускаются сами: тест выполняет их, когда нужно."""

    def run(self):
        while self:
            self.pop(0)()


def ready(client, deps, solves):
    background = Background()
    deps.run_background = background.append
    base = f"/api/datasets/{upload(client, 'bundle.json', sample_bundle().model_dump_json().encode())}"
    assert client.get(base).json()["status"] == "ready"
    solves.clear()
    return base, background


def dataset(tmp_path, solves, **options):
    client, deps = make_client(tmp_path, **options)
    base, background = ready(client, deps, solves)
    return client, deps, base, background


def body(event):
    return event.model_dump(mode="json")


def add(client, base, event):
    return client.post(f"{base}/timeline/events", json=body(event) if not isinstance(event, dict) else event)


def added(client, base, event):
    response = add(client, base, event)
    assert response.status_code == 200, response.text
    return response.json()


def at(client, base, time):
    response = client.post(f"{base}/cursor", json={"time": time})
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["cursor"] == time
    return state


def state_of(client, base):
    return client.get(f"{base}/state").json()


def statuses(state):
    return [(item["id"], item["status"]) for item in state["timeline"]]


def request_status(state, request_id):
    return next(request["status"] for request in state["requests"] if request["id"] == request_id)


def plan_part(state):
    """Состояние без полей таймлайна и номера датасета: план, заявки, события и версия на текущее время."""
    return {key: value for key, value in state.items() if key not in (*TIMELINE_FIELDS, "dataset_id")}


def planned(state):
    return sorted(visit["request_id"] for route in state["plan"]["routes"] for visit in route["visits"])


def test_old_bundle_day_cancellations_are_not_put_on_the_timeline(tmp_path, solves):
    """Отмены дня из контрольного файла больше не встают на шкалу: клиент отменял уже после приезда инженера."""
    bundle = json.loads(sample_bundle().model_dump_json())
    bundle["cancellations"] = [{"request_id": "R2", "time": "09:30"}, {"request_id": "R3", "time": "11:00"}]
    client, deps = make_client(tmp_path, bundle=sample_bundle())
    background = Background()
    deps.run_background = background.append
    base = f"/api/datasets/{upload(client, 'bundle.json', json.dumps(bundle).encode())}"
    assert client.get(base).json()["status"] == "ready"

    state = state_of(client, base)

    assert (state["cursor"], state["timeline"], state["events"]) == ("00:00", [], [])
    assert all(request["status"] == "active" for request in state["requests"])
    assert "R2" in planned(state) and "R3" in planned(state)
    # День целиком: заявки не отменяются и когда часы доходят до прежнего времени отмены.
    later = at(client, base, "12:00")
    assert later["timeline"] == [] and request_status(later, "R2") == "active" and "R2" in planned(later)
    # «Построить план» на нетронутой шкале тоже ничего на неё не кладёт.
    assert client.post(f"{base}/plan").json()["timeline"] == []

    # Ручная отмена работает как раньше: встаёт на шкалу и применяется, когда часы доходят до неё.
    added_state = added(client, base, cancel("R2", "13:00"))
    assert [item["event"]["request_id"] for item in added_state["timeline"]] == ["R2"]
    assert request_status(at(client, base, "14:00"), "R2") == "cancelled"


def test_state_after_upload_has_cursor_at_midnight_and_empty_timeline(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    state = state_of(client, base)
    assert (state["cursor"], state["timeline"], state["timeline_ready"]) == ("00:00", [], True)
    assert client.post(f"{base}/plan").json() == state


def test_future_event_is_pending_and_computed_in_background(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    initial = state_of(client, base)

    state = added(client, base, cancel("R2", "13:00"))

    assert state["timeline"] == [
        {
            "id": "tl_1",
            "event": body(cancel("R2", "13:00")),
            "status": "pending",
            "reason": None,
            "variant": None,
            "choosable": False,
        }
    ]
    assert (state["cursor"], state["timeline_ready"]) == ("00:00", False)
    assert plan_part(state) == plan_part(initial)
    assert solves == [] and len(background) == 1

    background.run()

    assert solves == ["13:00"]
    after = state_of(client, base)
    assert after["timeline_ready"] is True and statuses(after) == [("tl_1", "pending")]
    assert plan_part(after) == plan_part(initial)


def test_cursor_before_and_after_events_recomputes_only_when_the_applied_set_changes(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    initial = state_of(client, base)
    added(client, base, cancel("R2", "13:00"))
    added(client, base, cancel("R3", "13:00"))

    before = at(client, base, "12:59")
    assert plan_part(before) == plan_part(initial) and solves == []
    assert statuses(before) == [("tl_1", "pending"), ("tl_2", "pending")]

    after = at(client, base, "13:00")
    # События ровно в текущее время уже применены.
    assert statuses(after) == [("tl_1", "applied"), ("tl_2", "applied")]
    assert (request_status(after, "R2"), request_status(after, "R3")) == ("cancelled", "cancelled")
    assert [applied["id"] for applied in after["events"]] == ["ev_1", "ev_2"]
    assert (after["now"], after["version"]) == ("13:00", 3)
    assert [item["event"] for item in after["timeline"]] == [applied["event"] for applied in after["events"]]
    assert solves == ["13:00", "13:00"]
    solves.clear()

    assert plan_part(at(client, base, "12:00")) == plan_part(initial)
    assert plan_part(at(client, base, "18:00")) == plan_part(after)
    assert plan_part(at(client, base, "00:00")) == plan_part(initial)
    assert plan_part(at(client, base, "13:00")) == plan_part(after)
    assert solves == []


def test_timeline_replay_equals_sequential_events(tmp_path, solves):
    client, deps, legacy, _ = dataset(tmp_path, solves)
    replayed, _ = ready(client, deps, solves)
    events = [cancel("R3", "11:00"), cancel("R2", "14:00")]
    for event in events:
        assert client.post(f"{legacy}/events", json=body(event)).status_code == 200
    sequential = state_of(client, legacy)
    for event in reversed(events):
        added(client, replayed, event)

    state = at(client, replayed, "15:00")

    assert plan_part(state) == plan_part(sequential)
    assert (sequential["cursor"], sequential["version"]) == ("14:00", 3)
    assert statuses(sequential) == [("tl_1", "applied"), ("tl_2", "applied")]
    assert statuses(state) == [("tl_2", "applied"), ("tl_1", "applied")]


def test_event_added_before_the_cursor_recomputes_only_later_snapshots(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    added(client, base, cancel("R3", "11:00"))
    added(client, base, cancel("R2", "14:00"))
    at(client, base, "15:00")
    assert solves == ["11:00", "14:00"]
    solves.clear()

    state = added(client, base, restore("R3", "12:00"))

    assert solves == ["12:00", "14:00"]
    assert statuses(state) == [("tl_1", "applied"), ("tl_3", "applied"), ("tl_2", "applied")]
    assert (request_status(state, "R3"), request_status(state, "R2")) == ("active", "cancelled")
    assert (state["cursor"], state["version"], state["timeline_ready"]) == ("15:00", 5, True)
    solves.clear()

    early = at(client, base, "11:30")
    assert (early["version"], request_status(early, "R3")) == (2, "cancelled") and solves == []


def test_delete_pending_and_applied_events(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    initial = state_of(client, base)
    added(client, base, cancel("R3", "11:00"))
    added(client, base, cancel("R2", "14:00"))
    at(client, base, "12:00")
    background.run()
    assert solves == ["11:00", "14:00"]
    solves.clear()

    missing = client.delete(f"{base}/timeline/events/tl_9")
    assert missing.status_code == 404 and missing.json()["detail"] == "Событие tl_9 не найдено."

    pending_deleted = client.delete(f"{base}/timeline/events/tl_2")
    assert pending_deleted.status_code == 200, pending_deleted.text
    assert statuses(pending_deleted.json()) == [("tl_1", "applied")]

    applied_deleted = client.delete(f"{base}/timeline/events/tl_1").json()
    assert plan_part(applied_deleted) == plan_part(initial)
    assert (applied_deleted["timeline"], applied_deleted["cursor"]) == ([], "12:00")
    assert solves == []


def test_deleting_an_applied_event_recomputes_later_applied_events(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    added(client, base, cancel("R3", "11:00"))
    added(client, base, cancel("R2", "14:00"))
    at(client, base, "15:00")
    solves.clear()

    response = client.delete(f"{base}/timeline/events/tl_1")

    assert response.status_code == 200, response.text
    state = response.json()
    assert solves == ["14:00"]
    assert statuses(state) == [("tl_2", "applied")]
    assert (request_status(state, "R3"), request_status(state, "R2")) == ("active", "cancelled")
    assert [applied["id"] for applied in state["events"]] == ["ev_1"] and state["version"] == 4


def test_rejected_event_gets_status_and_reason_and_is_skipped(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    added(client, base, cancel("R2", "11:00"))
    added(client, base, cancel("R2", "12:00"))
    background.run()
    assert solves == ["11:00"]

    state = state_of(client, base)
    assert state["timeline_ready"] is True
    assert [(item["id"], item["status"], item["reason"]) for item in state["timeline"]] == [
        ("tl_1", "pending", None),
        ("tl_2", "rejected", "Заявка R2 уже отменена."),
    ]
    assert state["timeline"][1]["event"] == body(cancel("R2", "12:00"))

    applied = at(client, base, "13:00")
    assert statuses(applied) == [("tl_1", "applied"), ("tl_2", "rejected")]
    assert len(applied["events"]) == 1 and solves == ["11:00"]

    deleted = client.delete(f"{base}/timeline/events/tl_2").json()
    assert statuses(deleted) == [("tl_1", "applied")] and solves == ["11:00"]
    assert plan_part(deleted) == plan_part(applied)


def test_event_rejected_at_or_before_the_cursor_is_removed_with_422(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    at(client, base, "13:00")
    added(client, base, cancel("R2", "12:00"))
    before = state_of(client, base)

    for time in ("12:30", "13:00"):
        rejected = add(client, base, cancel("R2", time))
        assert rejected.status_code == 422 and rejected.json()["detail"] == "Заявка R2 уже отменена."
    assert state_of(client, base) == before

    # Более раннее событие применяется, а прежнее отмечается отклонённым.
    earlier = added(client, base, cancel("R2", "11:00"))
    assert [(item["status"], item["event"]["time"]) for item in earlier["timeline"]] == [
        ("applied", "11:00"),
        ("rejected", "12:00"),
    ]
    assert earlier["timeline"][1]["id"] == "tl_1"
    assert earlier["timeline"][1]["reason"] == "Заявка R2 уже отменена."
    assert [applied["event"]["time"] for applied in earlier["events"]] == ["11:00"]


def test_timeline_event_checks_ids_and_time_range(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    urgent = {
        "type": "urgent",
        "time": "15:00",
        "request": {
            "id": "URG-1",
            "address": "Город Москва, ул.Таганская, д. 1",
            "lat": 55.751,
            "lon": 37.61,
            "duration_min": 30,
            "window_start": "15:00",
            "window_end": "17:00",
            "skill": "local",
        },
    }
    added(client, base, urgent)
    # В ту же минуту, что и срочная заявка: событие добавлено позже и применяется после неё, работа ещё не начата.
    added(client, base, cancel("URG-1", "15:00"))

    cases = [
        (body(cancel("URG-1", "14:00")), "Заявка URG-1 не найдена."),
        (body(cancel("NOPE", "13:00")), "Заявка NOPE не найдена."),
        ({"type": "engineer_unavailable", "time": "13:00", "engineer_id": "E9"}, "Инженер E9 не найден."),
        (
            {"type": "request_reassigned", "time": "13:00", "request_id": "R1", "engineer_id": "E9"},
            "Инженер E9 не найден.",
        ),
        (
            {"type": "request_reassigned", "time": "13:00", "request_id": "NOPE", "engineer_id": "E1"},
            "Заявка NOPE не найдена.",
        ),
        ({**urgent, "time": "17:00"}, "Заявка с номером URG-1 уже есть в плане."),
        ({**urgent, "request": {**urgent["request"], "id": "R1"}}, "Заявка с номером R1 уже есть в плане."),
        (body(cancel("R2", "24:00")), "Время события должно быть от 00:00 до 23:59."),
        (
            {"type": "cancel", "time": "13:00"},
            "Некорректный запрос: для отмены или возврата нужен request_id",
        ),
    ]
    for event, detail in cases:
        response = add(client, base, event)
        assert response.status_code == 422, event
        assert response.json()["detail"] == detail
    assert statuses(state_of(client, base)) == [("tl_1", "pending"), ("tl_2", "pending")]
    assert solves == []

    # Срочная заявка — «ломающее» событие: без выбора варианта время на ней остановится.
    assert client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "optimal"}).status_code == 200
    evening = at(client, base, "17:00")
    assert statuses(evening) == [("tl_1", "applied"), ("tl_2", "applied")]
    assert request_status(evening, "URG-1") == "cancelled"


def test_cursor_validation_and_missing_datasets(tmp_path, solves):
    client, deps, base, _ = dataset(tmp_path, solves)
    cases = [
        ({"time": "24:00"}, "Некорректный запрос: время должно быть от 00:00 до 23:59"),
        ({"time": "99:59"}, "Некорректный запрос: время должно быть от 00:00 до 23:59"),
        ({"time": "ab"}, "Некорректный запрос: time: ожидается HH:MM, получено 'ab'"),
        ({}, "Некорректный запрос: time: обязательное поле"),
    ]
    for payload, detail in cases:
        response = client.post(f"{base}/cursor", json=payload)
        assert response.status_code == 422 and response.json()["detail"] == detail
    assert at(client, base, "23:59")["cursor"] == "23:59"

    processing = deps.registry.create().dataset_id
    for dataset_id, code in (("d_missing", 404), (processing, 409)):
        prefix = f"/api/datasets/{dataset_id}"
        responses = [
            client.post(f"{prefix}/cursor", json={"time": "12:00"}),
            client.post(f"{prefix}/timeline/events", json=body(cancel("R2", "12:00"))),
            client.delete(f"{prefix}/timeline/events/tl_1"),
        ]
        assert [response.status_code for response in responses] == [code, code, code]


def test_legacy_events_use_the_cursor_and_keep_their_texts(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    at(client, base, "13:30")

    early = client.post(f"{base}/events", json=body(cancel("R2", "13:00")))
    assert early.status_code == 422
    assert early.json()["detail"] == "Время события 13:00 раньше текущего времени плана 13:30."

    # Работы по R2 (окно с 14:00) ещё не начаты.
    response = client.post(f"{base}/events", json=body(cancel("R2", "13:45")))
    assert response.status_code == 200, response.text
    applied = response.json()
    assert (applied["cursor"], applied["now"], applied["version"]) == ("13:45", "13:45", 2)
    assert statuses(applied) == [("tl_1", "applied")]

    # Отмена R3 впереди, до начала работ по ней (окно с 15:00): legacy-событие позже применяет её по пути.
    added(client, base, cancel("R3", "14:45"))
    solves.clear()
    rejected = client.post(f"{base}/events", json=body(cancel("R3", "17:00")))
    assert rejected.status_code == 422 and rejected.json()["detail"] == "Заявка R3 уже отменена."
    assert solves == ["14:45"]
    state = state_of(client, base)
    assert (state["cursor"], state["version"]) == ("13:45", 2)
    assert statuses(state) == [("tl_1", "applied"), ("tl_2", "pending")]

    solves.clear()
    later = at(client, base, "14:45")
    assert request_status(later, "R3") == "cancelled" and solves == []


def with_llm(tmp_path, solves, *responses):
    client, deps = make_client(tmp_path)
    provider = ScriptedProvider(*responses)
    deps.llm = provider.client()
    base, background = ready(client, deps, solves)
    return client, base, provider


def test_proposal_is_proposed_and_approved_at_the_cursor(tmp_path, solves):
    call = tool_call("propose_cancel", {"request_id": "R2", "rationale": "Клиент отменил визит"})
    client, base, provider = with_llm(tmp_path, solves, completion(tool_calls=[call]))
    at(client, base, "13:00")
    added(client, base, cancel("R3", "14:00"))

    [proposal] = client.post(f"{base}/chat", json={"text": "Отмена по R2"}).json()["proposals"]

    assert proposal["event"]["time"] == "13:00"
    system, user = provider.bodies()[0]["messages"]
    assert "Текущее время плана: 13:00." in system["content"] and '"now": "13:00"' in user["content"]

    result = client.post(f"{base}/proposals/{proposal['id']}/approve").json()
    state = result["state"]
    assert state["cursor"] == "13:00"
    assert statuses(state) == [("tl_2", "applied"), ("tl_1", "pending")]
    assert result["proposal"]["event"] == state["timeline"][0]["event"] == state["events"][-1]["event"]
    assert result["proposal"]["result_diff"] == state["last_diff"]
    assert (request_status(state, "R2"), request_status(state, "R3")) == ("cancelled", "active")
    assert request_status(at(client, base, "12:00"), "R2") == "active"
    evening = at(client, base, "17:00")
    assert (request_status(evening, "R2"), request_status(evening, "R3")) == ("cancelled", "cancelled")


def test_proposal_made_before_the_cursor_moved_is_applied_at_the_cursor(tmp_path, solves):
    call = tool_call("propose_cancel", {"request_id": "R2", "time": "13:00", "rationale": "Отказ клиента"})
    client, base, _ = with_llm(tmp_path, solves, completion(tool_calls=[call]))
    [proposal] = client.post(f"{base}/chat", json={"text": "R2 отменена"}).json()["proposals"]
    # Время сдвинули, но работы по R2 (окно с 14:00) ещё не начаты.
    at(client, base, "13:30")

    result = client.post(f"{base}/proposals/{proposal['id']}/approve").json()

    assert result["proposal"]["status"] == "approved" and result["proposal"]["event"]["time"] == "13:30"
    assert (result["state"]["cursor"], result["state"]["now"]) == ("13:30", "13:30")
    assert statuses(result["state"]) == [("tl_1", "applied")]


def test_plan_rebuild_clears_the_timeline_and_keeps_it_without_rebuild(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    moved = at(client, base, "13:00")
    assert client.post(f"{base}/plan").json() == moved

    added(client, base, cancel("R2", "14:00"))
    rebuilt = client.post(f"{base}/plan").json()

    assert (rebuilt["timeline"], rebuilt["cursor"], rebuilt["timeline_ready"]) == ([], "00:00", True)
    assert (rebuilt["version"], rebuilt["events"]) == (2, [])
    background.run()
    assert solves == ["00:00"]
    assert statuses(added(client, base, cancel("R2", "14:00"))) == [("tl_2", "pending")]


def test_clearing_the_timeline_returns_to_the_morning_plan_without_solving(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    morning = state_of(client, base)
    added(client, base, cancel("R2", "14:00"))
    at(client, base, "15:00")
    background.run()
    solves.clear()

    response = client.delete(f"{base}/timeline")

    assert response.status_code == 200, response.text
    cleared = response.json()
    assert (cleared["timeline"], cleared["cursor"], cleared["events"], cleared["pending_choice"]) == (
        [],
        "00:00",
        [],
        None,
    )
    assert cleared["plan"] == morning["plan"] and cleared["requests"] == morning["requests"]
    assert solves == []
    # «Построить план» с теми же значениями после сброса ничего не пересобирает.
    assert client.post(f"{base}/plan").json()["plan"] == morning["plan"] and solves == []
    assert statuses(added(client, base, cancel("R2", "14:00"))) == [("tl_2", "pending")]
    assert client.delete("/api/datasets/d_missing/timeline").status_code == 404


def test_versions_stay_unique_when_events_are_removed_and_added_again(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    at(client, base, "15:00")

    first = added(client, base, cancel("R2", "14:00"))
    busy = next(route["engineer_id"] for route in first["plan"]["routes"] if route["visits"])
    geometry = f"{base}/routes/{busy}/geometry"
    assert (first["version"], client.get(geometry).json()["version"]) == (2, 2)

    assert client.delete(f"{base}/timeline/events/tl_1").json()["version"] == 1
    assert client.get(geometry).json()["version"] == 1

    again = added(client, base, cancel("R2", "14:00"))
    assert (again["version"], client.get(geometry).json()["version"]) == (3, 3)


def test_background_precompute_stops_when_the_timeline_changes(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    added(client, base, cancel("R3", "11:00"))
    added(client, base, cancel("R2", "14:00"))
    assert len(background) == 2

    stale = background.pop(0)
    stale()
    assert solves == [] and state_of(client, base)["timeline_ready"] is False

    background.run()
    assert solves == ["11:00", "14:00"] and state_of(client, base)["timeline_ready"] is True

    at(client, base, "15:00")
    assert solves == ["11:00", "14:00"] and background == []


def test_cursor_request_during_precompute_waits_for_the_step_and_reuses_it(tmp_path, solves):
    client, deps = make_client(tmp_path)
    base, _ = ready(client, deps, solves)
    workers = []

    def run_in_thread(task):
        worker = threading.Thread(target=task, daemon=True)
        workers.append(worker)
        worker.start()

    deps.run_background = run_in_thread
    entered, release = solves.hold("14:00")
    added(client, base, cancel("R3", "11:00"))
    added(client, base, cancel("R2", "14:00"))
    assert entered.wait(timeout=30)

    assert state_of(client, base)["timeline_ready"] is False
    early = at(client, base, "12:00")
    assert (request_status(early, "R3"), request_status(early, "R2")) == ("cancelled", "active")

    results = {}
    late = threading.Thread(
        target=lambda: results.update(late=client.post(f"{base}/cursor", json={"time": "15:00"})), daemon=True
    )
    late.start()
    late.join(timeout=0.3)
    assert late.is_alive()
    release.set()
    late.join(timeout=30)
    for worker in workers:
        worker.join(timeout=30)

    response = results["late"]
    assert response.status_code == 200, response.text
    state = response.json()
    assert statuses(state) == [("tl_1", "applied"), ("tl_2", "applied")]
    assert (request_status(state, "R3"), request_status(state, "R2")) == ("cancelled", "cancelled")
    assert (solves.count("11:00"), solves.count("14:00")) == (1, 1)
    assert state_of(client, base)["timeline_ready"] is True


class CountingGeocoder(HashGeocoder):
    def __init__(self, category="building"):
        super().__init__(category)
        self.queries = []

    def lookup(self, query):
        self.queries.append(query)
        return super().lookup(query)


def test_timeline_edit_is_geocoded_once_when_added(tmp_path, solves):
    geocoder = CountingGeocoder(category="highway")
    client, _, base, _ = dataset(tmp_path, solves, geocoder=geocoder)
    before = next(request for request in state_of(client, base)["requests"] if request["id"] == "R2")
    moved = {**before, "address": "Город Москва, ул.Таганская, д. 7", "lat": None, "lon": None}
    edit = {"type": "request_updated", "time": "12:00", "request_id": "R2", "request": moved}

    added(client, base, edit)
    looked_up = len(geocoder.queries)
    assert looked_up > 0
    added(client, base, cancel("R3", "11:00"))
    at(client, base, "13:00")
    state = added(client, base, restore("R3", "11:30"))

    assert len(geocoder.queries) == looked_up
    assert statuses(state) == [("tl_2", "applied"), ("tl_3", "applied"), ("tl_1", "applied")]
    stored = next(request for request in state["requests"] if request["id"] == "R2")
    assert (stored["address"], stored["geocode_precision"]) == ("Город Москва, ул.Таганская, д. 7", "street")
    assert state["timeline"][2]["event"]["previous_request"]["address"] == before["address"]


def unavailable(engineer_id, time):
    return Event(type=EventType.ENGINEER_UNAVAILABLE, time=time, engineer_id=engineer_id)


def busy_of(client, base):
    return next(route["engineer_id"] for route in state_of(client, base)["plan"]["routes"] if route["visits"])


def cursor_to(client, base, time):
    response = client.post(f"{base}/cursor", json={"time": time})
    assert response.status_code == 200, response.text
    return response.json()


def test_time_stops_at_a_breaking_event_until_a_variant_is_chosen(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    busy = busy_of(client, base)
    added(client, base, unavailable(busy, "13:00"))
    added(client, base, cancel("R3", "14:30"))
    background.run()
    ahead = state_of(client, base)
    assert ahead["timeline_ready"] is True and ahead["pending_choice"] is None
    assert [(item["status"], item["choosable"], item["variant"]) for item in ahead["timeline"]] == [
        ("pending", True, None),
        ("pending", False, None),
    ]

    stopped = cursor_to(client, base, "17:00")
    assert stopped["cursor"] == "13:00"
    assert [item["status"] for item in stopped["timeline"]] == ["awaiting", "pending"]
    choice = stopped["pending_choice"]
    assert choice["entry_id"] == "tl_1" and choice["current"] is None
    assert [option["variant"] for option in choice["variants"]] == ["optimal", "stable", "keep"]
    assert [option["title"] for option in choice["variants"]] == [
        "Оптимально по дню",
        "Минимум перестановок",
        "Ничего не менять",
    ]
    assert sum(option["recommended"] for option in choice["variants"]) == 1
    assert stopped["plan"] == ahead["plan"]

    chosen = client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "keep"})
    assert chosen.status_code == 200, chosen.text
    chosen = chosen.json()
    assert (chosen["cursor"], chosen["pending_choice"]) == ("13:00", None)
    assert [(item["status"], item["variant"]) for item in chosen["timeline"]] == [
        ("applied", "keep"),
        ("pending", None),
    ]
    assert {item["request_id"] for item in chosen["plan"]["unassigned"]} >= {
        visit["request_id"]
        for route in ahead["plan"]["routes"]
        if route["engineer_id"] == busy
        for visit in route["visits"]
        if visit["start"] >= "13:00"
    }

    later = cursor_to(client, base, "17:00")
    assert [item["status"] for item in later["timeline"]] == ["applied", "applied"]

    changed = client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "optimal"}).json()
    assert (changed["cursor"], changed["timeline"][0]["variant"]) == ("17:00", "optimal")
    assert changed["plan"] != later["plan"]
    again = client.get(f"{base}/timeline/events/tl_1/variants")
    assert again.status_code == 200 and again.json()["current"] == "optimal"


def test_with_a_solver_pool_both_variants_of_an_event_are_solved_at_once(tmp_path, solves, monkeypatch):
    client, deps, base, background = dataset(tmp_path, solves)
    busy = busy_of(client, base)
    deps.ingest.planning.solver_pool = object()
    # Обе стратегии должны дойти до барьера одновременно, иначе через 10 секунд он ломается.
    together = threading.Barrier(2, timeout=10)

    def solve(problem, workload_level, time_limit_s, variant="optimal", pool=None, share=1):
        assert (pool, share) == (deps.ingest.planning.solver_pool, 2)
        together.wait()
        return solves.solve(problem, workload_level, time_limit_s, variant)

    monkeypatch.setattr("app.planning.session._solve", solve)
    added(client, base, unavailable(busy, "13:00"))
    background.run()
    assert sorted(solves.variants) == ["optimal", "stable"]
    stopped = cursor_to(client, base, "17:00")
    assert (stopped["cursor"], stopped["pending_choice"]["entry_id"]) == ("13:00", "tl_1")
    assert len(stopped["pending_choice"]["variants"]) == 3


def test_breaking_event_at_the_cursor_asks_for_a_variant_at_once(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    busy = busy_of(client, base)
    cursor_to(client, base, "12:00")
    state = added(client, base, unavailable(busy, "12:00"))
    assert state["cursor"] == "12:00"
    assert state["timeline"][0]["status"] == "awaiting"
    assert state["pending_choice"]["entry_id"] == "tl_1"


def test_variant_errors(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    added(client, base, cancel("R1", "16:00"))
    background.run()
    assert client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "keep"}).status_code == 409
    assert client.get(f"{base}/timeline/events/tl_1/variants").status_code == 409
    assert client.put(f"{base}/timeline/events/tl_9/variant", json={"variant": "keep"}).status_code == 404
    assert client.get(f"{base}/timeline/events/tl_9/variants").status_code == 404
    assert client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "best"}).status_code == 422
    # Строка без номера бригады тоже неизвестна: до решателя она не доходит.
    assert client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "assign:"}).status_code == 422


def test_legacy_events_and_proposals_apply_the_optimal_variant_without_asking(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    busy = busy_of(client, base)
    response = client.post(f"{base}/events", json=body(unavailable(busy, "13:00")))
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["pending_choice"] is None
    assert [(item["status"], item["variant"]) for item in state["timeline"]] == [("applied", "optimal")]


def test_legacy_event_after_an_event_without_a_choice_is_409_and_not_kept(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    added(client, base, unavailable(busy_of(client, base), "13:00"))

    response = client.post(f"{base}/events", json=body(cancel("R3", "14:30")))

    assert response.status_code == 409
    assert response.json()["detail"] == "Сначала выберите вариант для события в 13:00."
    state = state_of(client, base)
    assert (state["cursor"], statuses(state)) == ("00:00", [("tl_1", "pending")])


def reassigned(request_id, engineer_id, time):
    return Event(type=EventType.REQUEST_REASSIGNED, time=time, request_id=request_id, engineer_id=engineer_id)


def route_ids(state, engineer_id):
    route = next(route for route in state["plan"]["routes"] if route["engineer_id"] == engineer_id)
    return [visit["request_id"] for visit in route["visits"]]


def test_request_reassignment_waits_for_a_variant_and_pins_the_request(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    assert route_ids(state_of(client, base), "E1") == ["R1", "R2", "R3"]
    added(client, base, reassigned("R3", "E2", "12:00"))
    background.run()

    stopped = cursor_to(client, base, "17:00")

    assert (stopped["cursor"], stopped["timeline"][0]["status"], stopped["timeline"][0]["choosable"]) == (
        "12:00",
        "awaiting",
        True,
    )
    choice = stopped["pending_choice"]
    assert [(option["variant"], option["title"]) for option in choice["variants"]] == [
        ("optimal", "Оптимально по дню"),
        ("stable", "Минимум перестановок"),
        ("keep", "Вставить в маршрут"),
    ]
    assert (
        choice["variants"][2]["summary"]
        == "Бригада пропускает, на что не успевает, остальные маршруты как есть"
    )
    # Бригаду называет само событие, четвёртого варианта у него нет.
    assert choice["assignable"] is False
    assert [option["request_engineer_id"] for option in choice["variants"]] == ["E2", "E2", "E2"]

    chosen = choose(client, base, "tl_1", "keep")

    assert (chosen["cursor"], chosen["timeline"][0]["status"], chosen["timeline"][0]["variant"]) == (
        "12:00",
        "applied",
        "keep",
    )
    assert chosen["timeline"][0]["event"]["previous_engineer_id"] == "E1"
    assert (
        next(request for request in chosen["requests"] if request["id"] == "R3")["fixed_engineer_id"] == "E2"
    )
    assert (route_ids(chosen, "E1"), route_ids(chosen, "E2")) == (["R1", "R2"], ["R3"])

    # Отказ одинаков для всех вариантов: выбора не требует, на шкале событие не остаётся.
    rejected = add(client, base, reassigned("R3", "E2", "12:00"))
    assert (rejected.status_code, rejected.json()["detail"]) == (422, "Заявка R3 уже у Инженер E2.")
    assert statuses(state_of(client, base)) == [("tl_1", "applied")]


def delayed(engineer_id, time, minutes):
    return Event(type=EventType.ENGINEER_DELAYED, time=time, engineer_id=engineer_id, delay_min=minutes)


def choose(client, base, entry_id, variant):
    response = client.put(f"{base}/timeline/events/{entry_id}/variant", json={"variant": variant})
    assert response.status_code == 200, response.text
    return response.json()


def cached_steps(deps, base):
    return set(deps.registry.get(base.rsplit("/", 1)[1]).timeline.steps)


def test_background_precompute_stops_at_the_first_event_without_a_choice(tmp_path, solves):
    client, deps, base, background = dataset(tmp_path, solves)
    added(client, base, unavailable(busy_of(client, base), "13:00"))
    added(client, base, cancel("R3", "14:30"))

    background.run()

    # Посчитаны три варианта события в 13:00 («ничего не менять» без решателя), отмена после него не считалась.
    assert cached_steps(deps, base) == {((), f"tl_1@{variant}") for variant in ("optimal", "stable", "keep")}
    assert (solves, solves.variants) == (["13:00", "13:00"], ["optimal", "stable"])


def test_changing_an_earlier_choice_replays_a_later_breaking_event_with_its_own_choice(tmp_path, solves):
    client, deps, base, background = dataset(tmp_path, solves)
    busy = busy_of(client, base)
    other = next(
        route["engineer_id"]
        for route in state_of(client, base)["plan"]["routes"]
        if route["engineer_id"] != busy
    )
    added(client, base, unavailable(busy, "13:00"))
    added(client, base, delayed(other, "14:00", 30))
    background.run()
    assert cursor_to(client, base, "17:00")["pending_choice"]["entry_id"] == "tl_1"
    choose(client, base, "tl_1", "keep")
    assert cursor_to(client, base, "17:00")["pending_choice"]["entry_id"] == "tl_2"
    choose(client, base, "tl_2", "stable")
    assert statuses(cursor_to(client, base, "17:00")) == [("tl_1", "applied"), ("tl_2", "applied")]
    solves.clear()

    changed = choose(client, base, "tl_1", "optimal")

    assert (changed["cursor"], changed["pending_choice"]) == ("17:00", None)
    assert [(item["status"], item["variant"]) for item in changed["timeline"]] == [
        ("applied", "optimal"),
        ("applied", "stable"),
    ]
    # Шаг «Оптимально» в 13:00 уже был в кэше, задержка пересчитана после него и со своей стратегией.
    assert (solves, solves.variants) == (["14:00"], ["stable"])
    assert (("tl_1@optimal",), "tl_2@stable") in cached_steps(deps, base)


def urgent(time="12:00", request_id="U1"):
    return Event(type=EventType.URGENT, time=time, request=req(request_id, 0, 0, "13:00", "17:00"))


def test_urgent_request_can_be_given_to_a_named_brigade_and_that_plan_is_counted_on_demand(tmp_path, solves):
    client, deps, base, background = dataset(tmp_path, solves)
    added(client, base, urgent())

    background.run()

    # Предподсчёт считает только три базовых варианта: «отдать бригаде» ждёт, пока диспетчер назовёт бригаду.
    assert cached_steps(deps, base) == {((), f"tl_1@{variant}") for variant in ("optimal", "stable", "keep")}
    choice = cursor_to(client, base, "17:00")["pending_choice"]
    assert (choice["assignable"], len(choice["variants"])) == (True, 3)
    assert choice["variants"][0]["request_engineer_id"] == "E1"
    solves.clear()

    response = client.get(f"{base}/timeline/events/tl_1/variants", params={"assign": "E2"})

    assert response.status_code == 200, response.text
    options = response.json()["variants"]
    assert [option["variant"] for option in options] == ["optimal", "stable", "keep", "assign:E2"]
    given = options[3]
    assert (given["title"], given["summary"]) == ("Отдать: Инженер E2", "Выбор диспетчера")
    assert (given["recommended"], given["compared_to"]) == (False, "optimal")
    assert given["request_engineer_id"] == "E2"
    # Посчитан ровно один план, и он лежит в кэше под своим ключом: второй раз окно откроется без решателя.
    assert (solves, ((), "tl_1@assign:E2") in cached_steps(deps, base)) == (["12:00"], True)
    solves.clear()
    # Выбор другого варианта делает проход полным и чистит кэш от лишних веток: посчитанный план бригады
    # в нём остаётся, и окно второй раз открывается без решателя.
    choose(client, base, "tl_1", "optimal")
    assert client.get(f"{base}/timeline/events/tl_1/variants", params={"assign": "E2"}).status_code == 200
    assert solves == []

    chosen = choose(client, base, "tl_1", "assign:E2")

    assert [(item["status"], item["variant"]) for item in chosen["timeline"]] == [("applied", "assign:E2")]
    assert next(item for item in chosen["requests"] if item["id"] == "U1")["fixed_engineer_id"] == "E2"
    assert "U1" in route_ids(chosen, "E2")


def test_a_brigade_can_be_named_only_for_an_urgent_request_and_only_from_this_day(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    added(client, base, unavailable(busy_of(client, base), "13:00"))
    added(client, base, urgent("14:00"))
    background.run()

    for response in (
        client.get(f"{base}/timeline/events/tl_1/variants", params={"assign": "E2"}),
        client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "assign:E2"}),
    ):
        assert (response.status_code, response.json()["detail"]) == (
            422,
            "Бригаду можно выбрать только для срочной заявки.",
        )
    for response in (
        client.get(f"{base}/timeline/events/tl_2/variants", params={"assign": "E9"}),
        client.put(f"{base}/timeline/events/tl_2/variant", json={"variant": "assign:E9"}),
    ):
        assert (response.status_code, response.json()["detail"]) == (422, "Инженер E9 не найден.")
