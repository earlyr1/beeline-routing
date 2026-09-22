"""Согласованные окна вкладки «Коммуникации»: что клиенту сказали по телефону, помнит сервер."""

from tests.api_helpers import sample_bundle, upload

WINDOW = {"window": {"start": "14:00", "end": "16:00", "asap": False}, "version": 1}


def day(api):
    client, deps = api()
    deps.run_background = lambda task: None
    dataset_id = upload(client, "bundle.json", sample_bundle().model_dump_json().encode())
    assert client.get(f"/api/datasets/{dataset_id}").json()["status"] == "ready"
    return client, f"/api/datasets/{dataset_id}"


def test_the_window_named_to_the_client_is_kept_and_shown_to_everyone(api, restarted):
    client, base = day(api)
    state = client.put(f"{base}/agreed/R2", json=WINDOW).json()
    assert state["agreed"]["R2"] == {
        "window": {"start": "14:00", "end": "16:00", "asap": False},
        "request_window": None,
        "version": 1,
    }
    # Другая вкладка спрашивает состояние и видит ту же отметку: она уже не в браузере одного человека.
    assert client.get(f"{base}/state").json()["agreed"]["R2"]["window"]["start"] == "14:00"
    # И перезапуск сервиса — тоже: договорённость с клиентом переживает его целиком.
    restarted(client, f"{base}/state")


def test_today_we_are_not_coming_is_an_agreement_too(api):
    client, base = day(api)
    state = client.put(f"{base}/agreed/R3", json={"window": None, "version": 1}).json()
    assert state["agreed"]["R3"]["window"] is None


def test_the_mark_can_be_taken_back(api):
    client, base = day(api)
    client.put(f"{base}/agreed/R2", json=WINDOW)
    assert client.delete(f"{base}/agreed/R2").json()["agreed"] == {}
    assert client.delete(f"{base}/agreed/R2").status_code == 404


def test_an_unknown_request_is_not_agreed_about(api):
    client, base = day(api)
    response = client.put(f"{base}/agreed/R404", json=WINDOW)
    assert response.status_code == 404 and "R404" in response.json()["detail"]


def test_a_day_planned_anew_has_no_calls_behind_it(api):
    client, base = day(api)
    client.put(f"{base}/agreed/R2", json=WINDOW)
    assert client.post(f"{base}/plan", json={"workload_level": 0}).json()["agreed"] == {}


def test_resetting_the_events_resets_the_calls(api):
    client, base = day(api)
    client.put(f"{base}/agreed/R2", json=WINDOW)
    assert client.delete(f"{base}/timeline").json()["agreed"] == {}
