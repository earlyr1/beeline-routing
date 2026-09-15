import json
import threading

from app.api.registry import DatasetRecord
from app.domain.models import Bundle
from app.ingest.geocode import GeoHit
from tests.api_helpers import csv_bytes, make_client, sample_bundle, upload
from tests.planning_helpers import OFFICE, day_engineers, transit_requests


def _ready_dataset(client):
    dataset_id = upload(client, "bundle.json", sample_bundle().model_dump_json().encode())
    status = client.get(f"/api/datasets/{dataset_id}").json()
    assert status["status"] == "ready", status
    return dataset_id


def test_health_and_config(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/config").json() == {
        "yandex_maps_api_key": "test-key",
        "llm_enabled": False,
        "osrm_available": False,
    }
    assert client.get("/api/openapi.json").json()["info"]["title"] == "Планировщик выездных инженеров"
    assert client.get("/api/docs").status_code == 200


def test_upload_bundle_then_plan(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post(
        "/api/upload",
        files={"file": ("bundle.json", sample_bundle().model_dump_json().encode(), "application/json")},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "processing"
    dataset_id = response.json()["dataset_id"]

    status = client.get(f"/api/datasets/{dataset_id}").json()
    assert status["status"] == "ready" and status["stage"] == "ready"
    assert status["report"]["source"] == "bundle"
    assert status["report"]["requests"] == 3 and status["report"]["matrix_source"] == "haversine"

    state = client.post(f"/api/datasets/{dataset_id}/plan").json()
    assert state["version"] == 1 and state["now"] == "00:00"
    assert state["plan"]["solver"] == "ortools" and state["baseline"]["solver"] == "fcfs"
    visits = [visit for route in state["plan"]["routes"] for visit in route["visits"]]
    assert visits and all(len(visit["start"]) == 5 and visit["start"][2] == ":" for visit in visits)
    assert client.get(f"/api/datasets/{dataset_id}/state").json()["version"] == 1


def test_events_explain_geometry_and_replan(tmp_path):
    client, _ = make_client(tmp_path)
    dataset_id = _ready_dataset(client)
    base = f"/api/datasets/{dataset_id}"

    assert client.get(f"{base}/routes/E1/geometry", params={"plan": "previous"}).status_code == 404

    response = client.post(f"{base}/events", json={"type": "cancel", "time": "13:00", "request_id": "R2"})
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["version"] == 2 and state["now"] == "13:00"
    removed = state["last_diff"]["removed"]
    assert [(item["request_id"], item["reason"]) for item in removed] == [("R2", "Заявка отменена")]
    assert state["events"][0]["id"] == "ev_1"

    explanation = client.get(f"{base}/explain/R3").json()
    assert explanation["status"] == "assigned"
    assert [c["name"] for c in explanation["constraints"]] == [
        "Навык",
        "Транспорт",
        "Временное окно",
        "Смена",
    ]
    assert client.get(f"{base}/explain/NOPE").status_code == 404

    busy = next(r["engineer_id"] for r in state["plan"]["routes"] if r["visits"])
    geometry = client.get(f"{base}/routes/{busy}/geometry").json()
    assert geometry["source"] == "straight" and geometry["legs"][0]["to_request_id"] == "R1"
    assert len(geometry["legs"][0]["coordinates"]) == 2
    assert client.get(f"{base}/routes/{busy}/geometry", params={"plan": "previous"}).status_code == 200

    replanned = client.post(f"{base}/plan").json()
    assert replanned["version"] == 3 and replanned["events"] == [] and replanned["now"] == "00:00"


def test_event_errors_are_russian_422(tmp_path):
    client, _ = make_client(tmp_path)
    dataset_id = _ready_dataset(client)
    base = f"/api/datasets/{dataset_id}/events"

    invalid = client.post(base, json={"type": "cancel", "time": "13:00"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "Некорректный запрос: для отмены или возврата нужен request_id"

    rejected = client.post(base, json={"type": "cancel", "time": "13:00", "request_id": "NOPE"})
    assert rejected.status_code == 422 and rejected.json()["detail"] == "Заявка NOPE не найдена."


def test_upload_csv_with_known_ids_reuses_region_bundle(tmp_path):
    client, _ = make_client(tmp_path)
    rows = [(r.id, "10:00", "12:00", r.address) for r in sample_bundle().requests]
    dataset_id = upload(client, "east.csv", csv_bytes(rows))
    status = client.get(f"/api/datasets/{dataset_id}").json()
    assert status["status"] == "ready", status
    assert status["report"]["source"] == "beeline_csv" and status["report"]["region"] == "t"
    assert status["report"]["skipped_rows"] == []


def test_upload_csv_with_new_ids_geocodes_and_detects_region_by_district(tmp_path):
    client, _ = make_client(tmp_path)
    rows = [
        ("N1", "10:00", "12:00", "Город Москва, ул.Таганская, д. 1"),
        ("N2", "14:00", "16:00", "Город Москва, ул.Марксистская, д. 5"),
    ]
    dataset_id = upload(client, "new.csv", csv_bytes(rows, office=None))
    status = client.get(f"/api/datasets/{dataset_id}").json()
    assert status["status"] == "ready", status
    assert status["progress"] == {"done": 2, "total": 2}
    assert status["report"]["geocoding"] == {"house": 2, "street": 0, "locality": 0, "none": 0}
    state = client.post(f"/api/datasets/{dataset_id}/plan").json()
    assert {r["id"] for r in state["requests"]} == {"N1", "N2"} and state["control"] is None


def test_upload_errors(tmp_path):
    client, deps = make_client(tmp_path)
    assert client.post("/api/upload", files={"file": ("x.txt", b"abc", "text/plain")}).status_code == 400
    assert client.post("/api/upload", files={"file": ("x.csv", b"", "text/csv")}).status_code == 400
    control = (
        "Заявка;Тип заявки BK;Статус BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Бригада\r\n"
        "1;Локальная заявка;Выполнена;Нет линка;17.08.2026 10:00;17.08.2026 12:00;Таганский;адрес;Бригада А\r\n"
    )
    dataset_id = upload(client, "control.csv", control.encode("utf-8"))
    status = client.get(f"/api/datasets/{dataset_id}").json()
    assert status["status"] == "failed" and "Контрольное распределение" in status["error"]
    assert client.post(f"/api/datasets/{dataset_id}/plan").status_code == 409
    assert client.get("/api/datasets/d_missing").status_code == 404

    pending = deps.registry.create()
    assert isinstance(pending, DatasetRecord)
    assert client.get(f"/api/datasets/{pending.dataset_id}/state").status_code == 409


def test_bundle_with_repeated_request_ids_is_rejected(tmp_path):
    client, _ = make_client(tmp_path)
    data = sample_bundle().model_dump(mode="json")
    data["requests"].append(dict(data["requests"][0], lat=55.8))
    dataset_id = upload(client, "bundle.json", json.dumps(data).encode())
    status = client.get(f"/api/datasets/{dataset_id}").json()
    assert status["status"] == "failed"
    assert status["error"] == "JSON не соответствует схеме бандла: повторяются номера заявок: R1"


def test_csv_with_repeated_request_ids_keeps_first_row_and_reports_the_rest(tmp_path):
    client, _ = make_client(tmp_path)
    rows = [
        ("N1", "10:00", "12:00", "Город Москва, ул.Таганская, д. 1"),
        ("N1", "14:00", "16:00", "Город Москва, ул.Таганская, д. 3"),
        ("N2", "14:00", "16:00", "Город Москва, ул.Марксистская, д. 5"),
    ]
    dataset_id = upload(client, "new.csv", csv_bytes(rows, office=None))
    status = client.get(f"/api/datasets/{dataset_id}").json()
    assert status["status"] == "ready", status
    assert status["report"]["requests"] == 2
    assert status["report"]["skipped_rows"] == ["строка 3: номер заявки N1 повторяется, строка пропущена"]
    state = client.post(f"/api/datasets/{dataset_id}/plan").json()
    planned = [visit["request_id"] for route in state["plan"]["routes"] for visit in route["visits"]]
    unassigned = [item["request_id"] for item in state["plan"]["unassigned"]]
    assert sorted(planned + unassigned) == ["N1", "N2"]


class LockProbeGeocoder:
    """Проверяет из другого потока, свободна ли блокировка датасета во время геокодирования."""

    def __init__(self):
        self.record = None
        self.lock_free = []

    def lookup(self, query):
        def probe():
            if self.record.lock.acquire(blocking=False):
                self.record.lock.release()
                self.lock_free.append(True)
            else:
                self.lock_free.append(False)

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join()
        return GeoHit(55.75, 37.61, "building")


def test_urgent_address_is_geocoded_before_taking_dataset_lock(tmp_path):
    geocoder = LockProbeGeocoder()
    client, deps = make_client(tmp_path, geocoder=geocoder)
    dataset_id = _ready_dataset(client)
    geocoder.record = deps.registry.get(dataset_id)
    request = {
        "id": "U1",
        "address": "Город Москва, ул.Таганская, д. 1",
        "district": "Таганский",
        "duration_min": 30,
        "window_start": "13:00",
        "window_end": "15:00",
        "skill": "emergency",
    }
    response = client.post(
        f"/api/datasets/{dataset_id}/events", json={"type": "urgent", "time": "13:00", "request": request}
    )
    assert response.status_code == 200, response.text
    assert geocoder.lock_free == [True]
    stored = geocoder.record.session.events[0].event.request
    assert (stored.lat, stored.lon, stored.geocode_precision) == (55.75, 37.61, "house")


def test_visit_on_the_way_is_not_shown_as_started_and_can_be_cancelled(tmp_path):
    """В 09:10 инженеры уже выехали к первым заявкам, но работа ещё не началась."""
    requests = [r.model_copy(update={"district": "Таганский"}) for r in transit_requests()]
    bundle = Bundle(region="t", office=OFFICE, requests=requests, engineers=day_engineers())
    client, _ = make_client(tmp_path, bundle=bundle)
    base = f"/api/datasets/{upload(client, 'bundle.json', bundle.model_dump_json().encode())}"
    plan = client.post(f"{base}/plan").json()["plan"]
    starts = {v["request_id"]: v["start"] for route in plan["routes"] for v in route["visits"]}
    assert sorted(starts) == ["A", "B", "C"] and min(starts.values()) > "09:10"

    response = client.post(f"{base}/events", json={"type": "cancel", "time": "09:10", "request_id": "C"})
    assert response.status_code == 200, response.text
    visits = [v for route in response.json()["plan"]["routes"] for v in route["visits"]]
    assert {v["request_id"]: v["start"] for v in visits} == {"A": starts["A"], "B": starts["B"]}
    assert [v["pinned"] for v in visits] == [False, False]

    cancelled = client.post(f"{base}/events", json={"type": "cancel", "time": "09:20", "request_id": "B"})
    assert cancelled.status_code == 200, cancelled.text
