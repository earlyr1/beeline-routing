"""POST /plan с уровнем нагрузки инженеров."""

from tests.api_helpers import make_client, sample_bundle, upload

LEVEL_TEXT = "Некорректный запрос: уровень нагрузки должен быть от 0 до 2"


def _ready_dataset(client):
    dataset_id = upload(client, "bundle.json", sample_bundle().model_dump_json().encode())
    assert client.get(f"/api/datasets/{dataset_id}").json()["status"] == "ready"
    return f"/api/datasets/{dataset_id}"


def test_plan_with_workload_level_rebuilds_day_and_keeps_level(tmp_path):
    client, _ = make_client(tmp_path)
    base = _ready_dataset(client)

    precomputed = client.post(f"{base}/plan").json()
    assert (precomputed["version"], precomputed["workload_level"]) == (1, 1)
    # Тот же уровень без событий: предподсчитанный план без изменений.
    assert client.post(f"{base}/plan", json={"workload_level": 1}).json() == precomputed

    calm = client.post(f"{base}/plan", json={"workload_level": 0}).json()
    assert (calm["version"], calm["workload_level"], calm["events"]) == (2, 0, [])
    assert calm["plan"]["solver"] == "ortools" and calm["baseline"]["solver"] == "fcfs"
    # Без тела и без поля остаётся уровень сессии.
    assert client.post(f"{base}/plan").json() == calm
    assert client.post(f"{base}/plan", json={}).json() == calm
    assert client.get(f"{base}/state").json()["workload_level"] == 0

    event = client.post(f"{base}/events", json={"type": "cancel", "time": "13:00", "request_id": "R2"})
    assert event.status_code == 200, event.text
    assert (event.json()["version"], event.json()["workload_level"]) == (3, 0)

    rebuilt = client.post(f"{base}/plan").json()
    assert (rebuilt["version"], rebuilt["workload_level"], rebuilt["events"]) == (4, 0, [])

    limit = client.post(f"{base}/plan", json={"workload_level": 2}).json()
    assert (limit["version"], limit["workload_level"]) == (5, 2)


def test_plan_rejects_workload_level_out_of_range(tmp_path):
    client, _ = make_client(tmp_path)
    base = _ready_dataset(client)
    for level in (-1, 3, 4):
        response = client.post(f"{base}/plan", json={"workload_level": level})
        assert response.status_code == 422
        assert response.json() == {"detail": LEVEL_TEXT}
    wrong_type = client.post(f"{base}/plan", json={"workload_level": "спокойный"})
    assert wrong_type.status_code == 422
    assert wrong_type.json()["detail"].startswith("Некорректный запрос: ")
    state = client.get(f"{base}/state").json()
    assert (state["version"], state["workload_level"]) == (1, 1)
