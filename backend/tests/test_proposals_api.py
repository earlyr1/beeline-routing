import httpx

from tests.api_helpers import HashGeocoder, make_client, sample_bundle, upload
from tests.llm_helpers import ScriptedProvider, completion, tool_call


def ready(client):
    dataset_id = upload(client, "bundle.json", sample_bundle().model_dump_json().encode())
    assert client.get(f"/api/datasets/{dataset_id}").json()["status"] == "ready"
    return f"/api/datasets/{dataset_id}"


def with_llm(tmp_path, *responses, geocoder=None):
    client, deps = make_client(tmp_path, geocoder=geocoder)
    provider = ScriptedProvider(*responses)
    deps.llm = provider.client()
    return client, provider


def test_chat_is_503_without_llm_and_config_says_disabled(tmp_path):
    client, _ = make_client(tmp_path)
    base = ready(client)
    response = client.post(f"{base}/chat", json={"text": "Инженер E1 заболел"})
    assert response.status_code == 503 and "LLM_BASE_URL" in response.json()["detail"]
    assert client.get("/api/config").json()["llm_enabled"] is False


def test_chat_creates_proposals_and_approve_goes_through_event_pipeline(tmp_path):
    calls = [
        tool_call(
            "propose_cancel", {"request_id": "R2", "time": "13:00", "rationale": "Клиент отменил визит"}
        ),
        tool_call("propose_engineer_unavailable", {"engineer_id": "E9", "rationale": "Не выйдет"}, "call_2"),
    ]
    client, provider = with_llm(tmp_path, completion(tool_calls=calls))
    base = ready(client)
    assert client.get("/api/config").json()["llm_enabled"] is True

    response = client.post(f"{base}/chat", json={"text": "  Отмена по R2, а E9 не выйдет  "})
    body = response.json()
    assert response.status_code == 200, body
    assert body["clarification"] == "Инженер «E9» не найден."
    [proposal] = body["proposals"]
    assert proposal["id"] == "pr_1" and proposal["status"] == "pending"
    assert proposal["event"] == {
        "type": "cancel",
        "time": "13:00",
        "request": None,
        "request_id": "R2",
        "engineer_id": None,
        "transport": None,
        "previous_transport": None,
        "previous_request": None,
    }
    assert proposal["source_text"] == "Отмена по R2, а E9 не выйдет" and proposal["created_at_version"] == 1
    messages = provider.bodies()[0]["messages"]
    assert "Текущее время плана: 00:00." in messages[0]["content"]
    assert messages[1]["content"].endswith("Отмена по R2, а E9 не выйдет")
    assert [p["id"] for p in client.get(f"{base}/proposals").json()] == ["pr_1"]

    approved = client.post(f"{base}/proposals/pr_1/approve")
    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert result["proposal"]["status"] == "approved"
    assert result["state"]["version"] == 2 and result["state"]["now"] == "13:00"
    assert result["proposal"]["result_diff"]["removed"][0]["request_id"] == "R2"
    assert client.get(f"{base}/state").json()["events"][0]["event"]["request_id"] == "R2"

    again = client.post(f"{base}/proposals/pr_1/approve")
    assert again.status_code == 409 and again.json()["detail"] == "Предложение pr_1 уже применено."
    assert client.post(f"{base}/proposals/pr_9/approve").status_code == 404


def test_urgent_proposal_is_geocoded_and_added_on_approve(tmp_path):
    arguments = {
        "address": "Город Москва, ул.Таганская, д. 3",
        "window_start": "13:00",
        "window_end": "16:00",
        "duration_min": 45,
        "skill": "local",
        "time": "13:00",
        "rationale": "Срочный вызов",
    }
    client, _ = with_llm(tmp_path, completion(tool_calls=[tool_call("propose_urgent_request", arguments)]))
    base = ready(client)
    [proposal] = client.post(f"{base}/chat", json={"text": "Срочно на Таганскую, 3"}).json()["proposals"]
    assert proposal["status"] == "pending" and proposal["event"]["request"]["id"] == "URG-AI-001"
    assert proposal["event"]["request"]["lat"] is not None

    state = client.post(f"{base}/proposals/{proposal['id']}/approve").json()["state"]
    assert "URG-AI-001" in {request["id"] for request in state["requests"]}
    assert state["last_diff"]["added"][0]["request_id"] == "URG-AI-001"


def _request_of(state, request_id):
    return next(request for request in state["requests"] if request["id"] == request_id)


def test_approved_proposals_keep_the_applied_event(tmp_path):
    calls = [
        tool_call(
            "propose_request_update",
            {
                "request_id": "R2",
                "duration_min": 90,
                "time": "13:00",
                "rationale": "Работы займут полтора часа",
            },
        ),
        tool_call(
            "propose_engineer_transport_change",
            {"engineer_id": "E1", "transport": "bike", "time": "13:00", "rationale": "Пересел на велосипед"},
            "call_2",
        ),
    ]
    client, _ = with_llm(tmp_path, completion(tool_calls=calls))
    base = ready(client)
    before = _request_of(client.get(f"{base}/state").json(), "R2")
    edit, transport = client.post(
        f"{base}/chat", json={"text": "R2 на полтора часа, E1 на велосипеде"}
    ).json()["proposals"]
    assert (edit["status"], edit["event"]["previous_request"], edit["event"]["request"]["duration_min"]) == (
        "pending",
        before,
        90,
    )
    assert (transport["status"], transport["event"]["previous_transport"]) == ("pending", "car")

    # Пока предложения ждали, диспетчер сам сдвинул окно R2 и пересадил E1 на пешую работу.
    moved = {**before, "window_start": "15:00", "window_end": "17:00"}
    manual = [
        {"type": "request_updated", "time": "13:00", "request_id": "R2", "request": moved},
        {"type": "engineer_transport_changed", "time": "13:00", "engineer_id": "E1", "transport": "foot"},
    ]
    for event in manual:
        assert client.post(f"{base}/events", json=event).status_code == 200

    result = client.post(f"{base}/proposals/approve-all").json()
    by_id = {proposal["id"]: proposal for proposal in result["proposals"]}
    applied = [item["event"] for item in result["state"]["events"][-2:]]
    approved_edit, approved_transport = by_id[edit["id"]], by_id[transport["id"]]
    assert (approved_edit["status"], approved_transport["status"]) == ("approved", "approved")
    assert [approved_edit["event"], approved_transport["event"]] == applied
    assert approved_edit["event"]["previous_request"] == {
        **moved,
        "window_start": "15:00",
        "window_end": "17:00",
    }
    assert approved_edit["event"]["request"] == {**before, "duration_min": 90}
    assert (approved_transport["event"]["previous_transport"], approved_transport["event"]["transport"]) == (
        "foot",
        "bike",
    )


def test_address_change_proposal_keeps_geocoder_precision_after_approve(tmp_path):
    arguments = {
        "request_id": "R2",
        "address": "Город Москва, ул.Таганская, д. 7",
        "time": "13:00",
        "rationale": "Клиент переехал",
    }
    client, _ = with_llm(
        tmp_path,
        completion(tool_calls=[tool_call("propose_request_update", arguments)]),
        geocoder=HashGeocoder(category="highway"),
    )
    base = ready(client)
    before = _request_of(client.get(f"{base}/state").json(), "R2")
    [proposal] = client.post(f"{base}/chat", json={"text": "R2 теперь на Таганской, 7"}).json()["proposals"]
    located = proposal["event"]["request"]
    assert proposal["status"] == "pending" and located["geocode_precision"] == "street"
    assert (located["lat"], located["lon"]) != (before["lat"], before["lon"])

    approved = client.post(f"{base}/proposals/{proposal['id']}/approve").json()
    stored = _request_of(approved["state"], "R2")
    assert stored == located
    assert approved["proposal"]["event"]["request"] == stored
    assert approved["proposal"]["event"]["previous_request"] == before


def test_approve_all_survives_stale_proposals_and_reject_endpoints(tmp_path):
    cancels = [
        tool_call("propose_cancel", {"request_id": "R3", "time": "13:00", "rationale": "Отказ клиента"}),
        tool_call(
            "propose_cancel", {"request_id": "R2", "time": "13:00", "rationale": "Отказ клиента"}, "call_2"
        ),
    ]
    restore = [tool_call("propose_restore", {"request_id": "R2", "rationale": "Снова в силе"})]
    client, _ = with_llm(
        tmp_path,
        completion(tool_calls=cancels),
        completion(tool_calls=restore),
        completion(tool_calls=restore),
    )
    base = ready(client)
    assert [
        p["id"] for p in client.post(f"{base}/chat", json={"text": "R3 и R2 отменены"}).json()["proposals"]
    ] == [
        "pr_1",
        "pr_2",
    ]
    manual = client.post(f"{base}/events", json={"type": "cancel", "time": "13:30", "request_id": "R3"})
    assert manual.status_code == 200

    result = client.post(f"{base}/proposals/approve-all").json()
    by_id = {p["id"]: p for p in result["proposals"]}
    assert (by_id["pr_1"]["status"], by_id["pr_1"]["error"]) == ("failed", "Заявка R3 уже отменена.")
    assert by_id["pr_2"]["status"] == "approved" and by_id["pr_2"]["event"]["time"] == "13:30"
    assert result["state"]["version"] == 3

    client.post(f"{base}/chat", json={"text": "R2 снова в силе"})
    client.post(f"{base}/chat", json={"text": "Верните R2"})
    rejected = client.post(f"{base}/proposals/pr_3/reject").json()
    assert rejected["status"] == "rejected"
    assert client.post(f"{base}/proposals/pr_3/reject").json()["detail"] == "Предложение pr_3 уже отклонено."
    after = client.post(f"{base}/proposals/reject-all").json()
    assert [(p["id"], p["status"]) for p in after] == [
        ("pr_1", "failed"),
        ("pr_2", "approved"),
        ("pr_3", "rejected"),
        ("pr_4", "rejected"),
    ]
    assert client.get(f"{base}/state").json()["version"] == 3


def test_chat_errors_are_russian(tmp_path):
    client, _ = with_llm(
        tmp_path, httpx.Response(401, json={"error": {"message": "no"}}), completion(content="")
    )
    base = ready(client)
    empty = client.post(f"{base}/chat", json={"text": "   "})
    assert (
        empty.status_code == 422 and empty.json()["detail"] == "Некорректный запрос: text: сообщение пустое"
    )
    failed = client.post(f"{base}/chat", json={"text": "Арташкин заболел"})
    assert failed.status_code == 503 and "LLM_API_KEY" in failed.json()["detail"]
    nothing = client.post(f"{base}/chat", json={"text": "Как дела?"}).json()
    assert nothing["proposals"] == [] and nothing["clarification"].startswith(
        "Не нашёл в сообщении изменений"
    )
