import json

import httpx

from app.llm.interpret import PARTIAL_HINT, RESTORE_UNSUPPORTED, REWRITE_HINT
from tests.api_helpers import HashGeocoder, sample_bundle, upload
from tests.llm_helpers import ScriptedProvider, completion, tool_call


def ready(client):
    dataset_id = upload(client, "bundle.json", sample_bundle().model_dump_json().encode())
    assert client.get(f"/api/datasets/{dataset_id}").json()["status"] == "ready"
    return f"/api/datasets/{dataset_id}"


def with_llm(api, *responses, geocoder=None):
    client, deps = api(geocoder=geocoder)
    provider = ScriptedProvider(*responses)
    deps.llm = provider.client()
    return client, provider


def test_chat_is_503_without_llm_and_config_says_disabled(api):
    client, _ = api()
    base = ready(client)
    response = client.post(f"{base}/chat", json={"text": "Инженер E1 заболел"})
    assert response.status_code == 503 and "LLM_BASE_URL" in response.json()["detail"]
    assert client.get("/api/config").json()["llm_enabled"] is False


def test_chat_creates_proposals_and_approve_goes_through_event_pipeline(api):
    calls = [
        tool_call(
            "propose_cancel", {"request_id": "R2", "time": "13:00", "rationale": "Клиент отменил визит"}
        ),
        tool_call("propose_engineer_unavailable", {"engineer_id": "E9", "rationale": "Не выйдет"}, "call_2"),
    ]
    client, provider = with_llm(api, completion(tool_calls=calls))
    base = ready(client)
    assert client.get("/api/config").json()["llm_enabled"] is True

    response = client.post(f"{base}/chat", json={"text": "  Отмена по R2, а E9 не выйдет  "})
    body = response.json()
    assert response.status_code == 200, body
    # Отмена понята и уже в предложениях: заново диспетчер пишет только непонятое.
    assert body["clarification"] == f"Не понял: инженер «E9» не найден. {PARTIAL_HINT}"
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
        "delay_min": None,
        "previous_engineer_id": None,
        "agreed_window": None,
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


def test_urgent_proposal_is_geocoded_and_added_on_approve(api):
    arguments = {
        "address": "Город Москва, ул.Таганская, д. 3",
        "window_start": "13:00",
        "window_end": "16:00",
        "duration_min": 45,
        "skill": "local",
        "time": "13:00",
        "rationale": "Срочный вызов",
    }
    client, _ = with_llm(api, completion(tool_calls=[tool_call("propose_urgent_request", arguments)]))
    base = ready(client)
    [proposal] = client.post(f"{base}/chat", json={"text": "Срочно на Таганскую, 3"}).json()["proposals"]
    assert proposal["status"] == "pending" and proposal["event"]["request"]["id"] == "URG-AI-001"
    assert proposal["event"]["request"]["lat"] is not None

    state = client.post(f"{base}/proposals/{proposal['id']}/approve").json()["state"]
    assert "URG-AI-001" in {request["id"] for request in state["requests"]}
    assert state["last_diff"]["added"][0]["request_id"] == "URG-AI-001"


def _request_of(state, request_id):
    return next(request for request in state["requests"] if request["id"] == request_id)


def test_approved_proposals_keep_the_applied_event(api, restarted):
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
    client, _ = with_llm(api, completion(tool_calls=calls))
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

    # Пока предложения ждали, диспетчер сам сдвинул окно R2 и пересадил E1 на пешую работу. Прежнее значение «foot»
    # (старый клиент) принимается как общественный транспорт и пешком.
    moved = {**before, "window_start": "16:00", "window_end": "18:00"}
    manual = [
        {"type": "request_updated", "time": "13:00", "request_id": "R2", "request": moved},
        {"type": "engineer_transport_changed", "time": "13:00", "engineer_id": "E1", "transport": "foot"},
    ]
    for event in manual:
        assert client.post(f"{base}/events", json=event).status_code == 200
    state = client.get(f"{base}/state").json()
    assert state["events"][-1]["event"]["transport"] == "public"
    assert "foot" not in json.dumps(state)

    result = client.post(f"{base}/proposals/approve-all").json()
    by_id = {proposal["id"]: proposal for proposal in result["proposals"]}
    applied = [item["event"] for item in result["state"]["events"][-2:]]
    approved_edit, approved_transport = by_id[edit["id"]], by_id[transport["id"]]
    assert (approved_edit["status"], approved_transport["status"]) == ("approved", "approved")
    assert [approved_edit["event"], approved_transport["event"]] == applied
    assert approved_edit["event"]["previous_request"] == {
        **moved,
        "window_start": "16:00",
        "window_end": "18:00",
    }
    assert approved_edit["event"]["request"] == {**before, "duration_min": 90}
    assert (approved_transport["event"]["previous_transport"], approved_transport["event"]["transport"]) == (
        "public",
        "bike",
    )

    # Предложения помощника со своими статусами и принятым событием поднимаются из базы такими же.
    restarted(client, f"{base}/state", f"{base}/proposals")


def test_approved_delay_proposal_keeps_the_applied_event_and_forecast(api):
    arguments = {"engineer_id": "E2", "delay_min": 30, "time": "10:15", "rationale": "Работа затянулась"}
    client, _ = with_llm(api, completion(tool_calls=[tool_call("propose_engineer_delay", arguments)]))
    base = ready(client)
    [proposal] = client.post(f"{base}/chat", json={"text": "У Белузина работа затянулась на полчаса"}).json()[
        "proposals"
    ]
    assert proposal["status"] == "pending"
    assert (proposal["event"]["type"], proposal["event"]["engineer_id"], proposal["event"]["delay_min"]) == (
        "engineer_delayed",
        "E2",
        30,
    )
    # Пока предложение ждало, диспетчер сам сдвинул время плана: применяется оно с текущего времени.
    manual = client.post(f"{base}/events", json={"type": "cancel", "time": "10:20", "request_id": "R3"})
    assert manual.status_code == 200, manual.text

    approved = client.post(f"{base}/proposals/{proposal['id']}/approve").json()

    applied = approved["state"]["events"][-1]["event"]
    assert approved["proposal"]["status"] == "approved"
    assert approved["proposal"]["event"] == applied
    assert (applied["type"], applied["time"], applied["delay_min"]) == ("engineer_delayed", "10:20", 30)
    forecast = approved["proposal"]["result_diff"]["delay_forecast"]
    assert (forecast["engineer_id"], forecast["delay_min"]) == ("E2", 30)
    assert approved["state"]["last_diff"]["delay_forecast"] == forecast


def test_address_change_proposal_keeps_geocoder_precision_after_approve(api):
    arguments = {
        "request_id": "R2",
        "address": "Город Москва, ул.Таганская, д. 7",
        "time": "13:00",
        "rationale": "Клиент переехал",
    }
    client, _ = with_llm(
        api,
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


def test_approve_all_survives_stale_proposals_and_reject_endpoints(api):
    cancels = [
        tool_call("propose_cancel", {"request_id": "R3", "time": "13:00", "rationale": "Отказ клиента"}),
        tool_call(
            "propose_cancel", {"request_id": "R2", "time": "13:00", "rationale": "Отказ клиента"}, "call_2"
        ),
    ]
    unavailable = [tool_call("propose_engineer_unavailable", {"engineer_id": "E1", "rationale": "Заболел"})]
    delay = [
        tool_call("propose_engineer_delay", {"engineer_id": "E1", "delay_min": 30, "rationale": "Пробка"})
    ]
    client, _ = with_llm(
        api,
        completion(tool_calls=cancels),
        completion(tool_calls=unavailable),
        completion(tool_calls=delay),
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

    client.post(f"{base}/chat", json={"text": "Арташкин заболел"})
    client.post(f"{base}/chat", json={"text": "Арташкин застрял в пробке на полчаса"})
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


def test_chat_errors_are_russian(api):
    client, _ = with_llm(api, httpx.Response(401, json={"error": {"message": "no"}}), completion(content=""))
    base = ready(client)
    empty = client.post(f"{base}/chat", json={"text": "   "})
    assert (
        empty.status_code == 422 and empty.json()["detail"] == "Некорректный запрос: text: сообщение пустое"
    )
    failed = client.post(f"{base}/chat", json={"text": "Арташкин заболел"})
    assert failed.status_code == 503 and "LLM_API_KEY" in failed.json()["detail"]
    nothing = client.post(f"{base}/chat", json={"text": "Как дела?"}).json()
    assert nothing["proposals"] == [] and nothing["clarification"].startswith(
        "Не понял: в сообщении нет изменения плана"
    )
    assert nothing["clarification"].endswith(REWRITE_HINT)


def test_restoring_a_cancelled_request_gets_an_honest_answer_and_changes_nothing(api):
    # Модель промолчала или назвала прежний инструмент возврата: в обоих случаях диспетчер читает, что так нельзя.
    legacy = [tool_call("propose_restore", {"request_id": "R2", "rationale": "Снова в силе"})]
    client, _ = with_llm(api, completion(content=""), completion(tool_calls=legacy))
    base = ready(client)
    cancel = {"type": "cancel", "time": "13:00", "request_id": "R2"}
    assert client.post(f"{base}/events", json=cancel).status_code == 200
    version = client.get(f"{base}/state").json()["version"]

    for text in ("Вернуть заявку R2", "R2 снова в силе"):
        answer = client.post(f"{base}/chat", json={"text": text}).json()
        assert answer["proposals"] == []
        assert answer["clarification"] == RESTORE_UNSUPPORTED
    assert client.get(f"{base}/proposals").json() == []
    assert client.get(f"{base}/state").json()["version"] == version


def test_restore_refusal_comes_next_to_proposals_for_the_rest_of_the_message(api):
    calls = [
        tool_call("propose_engineer_unavailable", {"engineer_id": "E1", "rationale": "Заболел"}),
        tool_call("restore_not_supported", {"request_id": "R2"}, "call_2"),
    ]
    client, provider = with_llm(api, completion(tool_calls=calls))
    base = ready(client)
    answer = client.post(f"{base}/chat", json={"text": "E1 заболел, а заявку R2 верните"}).json()
    assert [proposal["event"]["engineer_id"] for proposal in answer["proposals"]] == ["E1"]
    assert answer["clarification"] == RESTORE_UNSUPPORTED
    assert "restore_not_supported" in [tool["function"]["name"] for tool in provider.bodies()[0]["tools"]]


def test_the_assistant_answers_not_understood_instead_of_a_question(api):
    """Памяти между сообщениями нет: вопрос помощника диспетчеру не на что ответить, поэтому вместо него «Не понял»."""
    reason = [tool_call("not_understood", {"reason": "не сказано, на сколько задерживается Белузин"})]
    legacy = [tool_call("ask_clarification", {"question": "На сколько задерживается Белузин?"})]
    client, provider = with_llm(api, completion(tool_calls=reason), completion(tool_calls=legacy))
    base = ready(client)

    answer = client.post(f"{base}/chat", json={"text": "Белузин опоздает"}).json()
    assert answer == {
        "proposals": [],
        "clarification": "Не понял: не сказано, на сколько задерживается Белузин. "
        "Напишите сообщение целиком ещё раз — прошлых сообщений помощник не помнит.",
    }
    # Модель со старой памятью назвала прежний инструмент вопроса: ответ той же формы.
    old = client.post(f"{base}/chat", json={"text": "Белузин опоздает"}).json()
    assert old["clarification"] == f"Не понял: на сколько задерживается Белузин. {REWRITE_HINT}"
    tools = [tool["function"]["name"] for tool in provider.bodies()[0]["tools"]]
    assert "not_understood" in tools and "ask_clarification" not in tools
    assert client.get(f"{base}/proposals").json() == []
