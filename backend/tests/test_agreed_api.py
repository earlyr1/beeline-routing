"""Отметка ✓ вкладки «Коммуникации» — событие «Коммуникация» (client_agreed) на шкале дня.

Что клиенту сказали по телефону, сервер знает из применённых к текущему времени событий: PlanningState.agreed —
номер заявки → названное окно (null — сегодня не приедем) и событие шкалы, удаление которого снимает отметку.
"""

from tests.api_helpers import sample_bundle, upload

# Клиенту называют слот сетки окон (п. 35 контракта): другое окно звонок не записывает.
WINDOW = {"start": "16:00", "end": "18:00", "asap": False}


def day(api):
    client, deps = api()
    deps.run_background = lambda task: None
    dataset_id = upload(client, "bundle.json", sample_bundle().model_dump_json().encode())
    assert client.get(f"/api/datasets/{dataset_id}").json()["status"] == "ready"
    base = f"/api/datasets/{dataset_id}"
    assert client.post(f"{base}/cursor", json={"time": "10:00"}).status_code == 200
    return client, base


def call(client, base, request_id, window, time="10:00"):
    event = {"type": "client_agreed", "time": time, "request_id": request_id, "agreed_window": window}
    return client.post(f"{base}/timeline/events", json=event)


def request_of(state, request_id):
    return next(request for request in state["requests"] if request["id"] == request_id)


def test_the_window_named_to_the_client_becomes_the_window_of_the_request(api, restarted):
    client, base = day(api)
    response = call(client, base, "R3", WINDOW)
    assert response.status_code == 200
    state = response.json()

    [item] = state["timeline"]
    assert (item["event"]["type"], item["status"]) == ("client_agreed", "applied")
    assert state["agreed"] == {"R3": {"window": WINDOW, "entry_id": item["id"]}}
    request = request_of(state, "R3")
    assert (request["window_start"], request["window_end"], request["asap"]) == ("16:00", "18:00", False)
    visit = next(v for route in state["plan"]["routes"] for v in route["visits"] if v["request_id"] == "R3")
    assert "16:00" <= visit["start"] <= "18:00"
    # Выбирать было не из чего: звонок проходит план с «Ничего не менять», окна выбора нет.
    assert (item["variant"], item["variant_auto"], state["pending_choice"]) == ("keep", True, None)
    # Другая вкладка и перезапуск сервиса видят ту же договорённость: она живёт на шкале.
    restarted(client, f"{base}/state")


def test_today_we_are_not_coming_postpones_the_request_and_counts_it_as_not_assigned(api):
    client, base = day(api)
    state = call(client, base, "R2", None).json()

    assert state["agreed"]["R2"]["window"] is None
    assert request_of(state, "R2")["status"] == "postponed"
    for plan in (state["plan"], state["baseline"]):
        [dropped] = plan["unassigned"]
        assert (dropped["request_id"], dropped["reason_code"]) == ("R2", "postponed")
        assert plan["metrics"]["unassigned"] == 1
        assert all(v["request_id"] != "R2" for route in plan["routes"] for v in route["visits"])


def test_the_clock_before_the_call_takes_its_effect_back(api):
    client, base = day(api)
    call(client, base, "R2", None)

    before = client.post(f"{base}/cursor", json={"time": "09:30"}).json()
    assert before["agreed"] == {}
    assert request_of(before, "R2")["status"] == "active"
    assert before["plan"]["unassigned"] == []

    again = client.post(f"{base}/cursor", json={"time": "11:00"}).json()
    assert again["agreed"]["R2"]["window"] is None
    assert request_of(again, "R2")["status"] == "postponed"


def test_taking_the_mark_back_deletes_the_event(api):
    client, base = day(api)
    entry_id = call(client, base, "R3", WINDOW).json()["agreed"]["R3"]["entry_id"]

    state = client.delete(f"{base}/timeline/events/{entry_id}").json()
    assert state["agreed"] == {} and state["timeline"] == []
    request = request_of(state, "R3")
    assert (request["window_start"], request["window_end"]) == ("15:00", "17:00")


def test_the_last_call_about_a_request_is_what_the_client_knows(api):
    client, base = day(api)
    call(client, base, "R2", None)
    # Звонок впереди часов ещё не случился: клиент пока знает прежнее.
    ahead = call(client, base, "R2", WINDOW, time="10:30").json()
    assert ahead["agreed"]["R2"]["window"] is None
    # Перезвонили и договорились на окно: «Ничего не менять» оставило бы вернувшуюся заявку без инженера, поэтому
    # часы встают на звонке и ждут выбора, а до выбора он не применён.
    waiting = client.post(f"{base}/cursor", json={"time": "10:30"}).json()
    entry_id = waiting["pending_choice"]["entry_id"]
    assert waiting["agreed"]["R2"]["window"] is None
    state = client.put(f"{base}/timeline/events/{entry_id}/variant", json={"variant": "optimal"}).json()
    assert state["agreed"]["R2"] == {"window": WINDOW, "entry_id": entry_id}
    assert request_of(state, "R2")["status"] == "active"


def test_a_window_outside_the_grid_is_not_named_to_the_client(api):
    """Звонок делает названное окно окном заявки, поэтому и оно — слот, как у правки заявки; мимо сетки не пройти."""
    client, base = day(api)
    response = call(client, base, "R3", {"start": "16:30", "end": "17:30", "asap": False})
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Окно заявки R3 16:30–17:30 не из сетки окон: клиенту называют слот. Выберите один из: 10:00–12:00, "
        "12:00–14:00, 14:00–16:00, 16:00–18:00, 18:00–20:00, 20:00–22:00."
    )
    assert client.get(f"{base}/state").json()["timeline"] == []


def test_an_asap_window_is_set_by_the_server_not_by_the_caller(api):
    """Окно «как можно скорее» задаёт сервер: обычная заявка ждёт с времени звонка до конца смен, что бы ни прислали."""
    client, base = day(api)
    state = call(client, base, "R3", {"start": "06:00", "end": "23:59", "asap": True}).json()
    request = request_of(state, "R3")
    assert (request["window_start"], request["asap"]) == ("10:00", True)
    assert request["window_end"] != "23:59"
    told = {"start": request["window_start"], "end": request["window_end"], "asap": True}
    assert state["agreed"]["R3"]["window"] == told


def test_a_call_about_an_unknown_request_is_refused(api):
    client, base = day(api)
    response = call(client, base, "R404", WINDOW)
    assert response.status_code == 422 and "R404" in response.json()["detail"]
    assert client.get(f"{base}/state").json()["timeline"] == []


def test_the_old_mark_endpoints_are_gone(api):
    client, base = day(api)
    assert client.put(f"{base}/agreed/R2", json={"window": WINDOW}).status_code in (404, 405)
    assert client.delete(f"{base}/agreed/R2").status_code in (404, 405)


def test_a_day_planned_anew_or_reset_has_no_calls_behind_it(api):
    client, base = day(api)
    call(client, base, "R2", WINDOW)
    assert client.post(f"{base}/plan", json={"workload_level": 0}).json()["agreed"] == {}

    client.post(f"{base}/cursor", json={"time": "10:00"})
    call(client, base, "R2", WINDOW)
    assert client.delete(f"{base}/timeline").json()["agreed"] == {}
