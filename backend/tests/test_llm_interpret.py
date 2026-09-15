from app.domain.enums import EventType, Priority, Skill, Transport
from app.ingest.geocode import GeoResult
from app.llm.client import LlmResult, ToolCall
from app.llm.interpret import NOTHING_FOUND, interpret
from app.llm.prompt import build_messages
from tests.llm_helpers import ids, named_session
from tests.planning_helpers import context


def run(calls, ctx=None, session=None, text=None):
    ctx = ctx or context()
    session = session or named_session(ctx)
    return interpret(LlmResult(calls=calls, text=text), session, ctx, ids())


def test_cancel_by_id_and_by_street_become_pending_drafts():
    out = run(
        [
            ToolCall(
                "propose_cancel", {"request_id": "R1", "time": "09:00", "rationale": "Клиент отказался"}
            ),
            ToolCall("propose_cancel", {"request_id": "Дубининская", "time": "13:00", "rationale": "Отмена"}),
        ]
    )
    assert out.clarifications == []
    assert [(d.event.type, d.event.request_id, d.event.time, d.error) for d in out.drafts] == [
        (EventType.CANCEL, "R1", 540, None),
        (EventType.CANCEL, "R2", 780, None),
    ]
    assert out.drafts[0].rationale == "Клиент отказался"


def test_engineer_resolved_by_surname_and_time_defaults_to_now():
    out = run([ToolCall("propose_engineer_unavailable", {"engineer_id": "белузин", "rationale": "Заболел"})])
    draft = out.drafts[0]
    assert (draft.event.engineer_id, draft.event.time, draft.error) == ("E2", 0, None)


def test_ambiguous_or_unknown_names_become_clarifications():
    out = run(
        [
            ToolCall("propose_engineer_unavailable", {"engineer_id": "Бригада", "rationale": "?"}),
            ToolCall("propose_cancel", {"request_id": "Тверская", "rationale": "?"}),
        ]
    )
    assert out.drafts == []
    assert out.clarifications == [
        "Под «Бригада» подходят несколько инженеров: Бригада Арташкин, Бригада Белузин. Уточните, кого вы имеете в виду.",
        "Заявка «Тверская» не найдена.",
    ]


def test_conflicts_with_day_state_become_failed_drafts():
    out = run(
        [ToolCall("propose_restore", {"request_id": "R2", "time": "13:00", "rationale": "Снова в силе"})]
    )
    assert out.drafts[0].error == "Заявка R2 не отменена, возвращать нечего."


def test_transport_change_by_surname_becomes_pending_draft():
    out = run(
        [
            ToolCall(
                "propose_engineer_transport_change",
                {
                    "engineer_id": "Арташкин",
                    "transport": "bike",
                    "time": "13:00",
                    "rationale": "Сломалась машина, пересел на велосипед",
                },
            ),
            ToolCall(
                "propose_engineer_transport_change",
                {"engineer_id": "белузин", "transport": "foot", "rationale": "Дальше пешком"},
            ),
        ]
    )
    assert out.clarifications == []
    assert [
        (
            d.event.type,
            d.event.engineer_id,
            d.event.previous_transport,
            d.event.transport,
            d.event.time,
            d.error,
        )
        for d in out.drafts
    ] == [
        (EventType.ENGINEER_TRANSPORT_CHANGED, "E1", Transport.CAR, Transport.BIKE, 780, None),
        (EventType.ENGINEER_TRANSPORT_CHANGED, "E2", Transport.CAR, Transport.FOOT, 0, None),
    ]
    assert out.drafts[0].rationale == "Сломалась машина, пересел на велосипед"


def test_transport_change_for_same_engineer_and_time_keeps_different_transports():
    arguments = {"engineer_id": "E1", "time": "13:00", "rationale": "Пересел"}
    out = run(
        [
            ToolCall("propose_engineer_transport_change", {**arguments, "transport": "bike"}),
            ToolCall("propose_engineer_transport_change", {**arguments, "transport": "foot"}),
            ToolCall("propose_engineer_transport_change", {**arguments, "transport": "bike"}),
        ]
    )
    assert [d.event.transport for d in out.drafts] == [Transport.BIKE, Transport.FOOT]


def test_transport_change_with_unknown_transport_or_same_transport():
    out = run(
        [
            ToolCall(
                "propose_engineer_transport_change",
                {"engineer_id": "Арташкин", "transport": "plane", "rationale": "Улетел"},
            ),
            ToolCall(
                "propose_engineer_transport_change",
                {"engineer_id": "Кузнецов", "transport": "bike", "rationale": "Пересел"},
            ),
            ToolCall(
                "propose_engineer_transport_change",
                {"engineer_id": "Белузин", "transport": "car", "time": "13:00", "rationale": "Выдали машину"},
            ),
        ]
    )
    assert out.clarifications == [
        "Не удалось разобрать предложение «propose_engineer_transport_change»: transport: неизвестный тип "
        "транспорта «plane», допустимы car, foot, bike, public.",
        "Инженер «Кузнецов» не найден.",
    ]
    [draft] = out.drafts
    assert (draft.event.engineer_id, draft.event.transport) == ("E2", Transport.CAR)
    assert draft.error == "У Бригада Белузин уже транспорт «Автомобиль»."


def test_urgent_request_is_geocoded_and_gets_generated_id():
    ctx = context(geocode=lambda address, district: GeoResult(55.75, 37.61, "house", address))
    arguments = {
        "address": "Москва, Дубининская улица, 59к2",
        "window_start": "13:00",
        "window_end": "15:00",
        "duration_min": 60,
        "skill": "emergency",
        "transport_required": "car",
        "time": "13:00",
        "rationale": "Авария на объекте",
    }
    out = run([ToolCall("propose_urgent_request", arguments)], ctx=ctx)
    request = out.drafts[0].event.request
    assert out.drafts[0].error is None
    assert (request.id, request.lat, request.lon, request.priority) == (
        "URG-AI-001",
        55.75,
        37.61,
        Priority.URGENT,
    )
    assert (request.skill, request.transport_required) == (Skill.EMERGENCY, Transport.CAR)

    no_transport = run(
        [ToolCall("propose_urgent_request", {**arguments, "transport_required": "none"})], ctx=ctx
    )
    assert no_transport.drafts[0].event.request.transport_required is None


def test_urgent_request_with_unknown_address_is_failed():
    ctx = context(geocode=lambda address, district: GeoResult(None, None, "none", None))
    arguments = {
        "address": "Нигде, д. 1",
        "window_start": "13:00",
        "window_end": "15:00",
        "duration_min": 60,
        "skill": "local",
        "rationale": "Срочно",
    }
    out = run([ToolCall("propose_urgent_request", arguments)], ctx=ctx)
    assert "не найден на карте" in out.drafts[0].error


def test_malformed_calls_duplicates_and_text_answers():
    out = run(
        [
            ToolCall(
                "propose_urgent_request",
                {
                    "address": "Москва, ул. Тестовая, 1",
                    "window_start": "15:00",
                    "window_end": "13:00",
                    "duration_min": 30,
                    "skill": "local",
                },
            ),
            ToolCall("propose_cancel", None, "аргументы не являются JSON (Expecting value)"),
            ToolCall("delete_everything", {}),
            ToolCall("ask_clarification", {"question": "Какую заявку отменить?"}),
            ToolCall("propose_cancel", {"request_id": "R3", "time": "13:00", "rationale": "Отмена"}),
            ToolCall("propose_cancel", {"request_id": "R3", "time": "13:00", "rationale": "Отмена ещё раз"}),
        ]
    )
    assert len(out.drafts) == 1
    assert out.clarifications[0].startswith("Не удалось разобрать предложение «propose_urgent_request»")
    assert out.clarifications[1:] == [
        "Не удалось разобрать предложение «propose_cancel»: аргументы не являются JSON (Expecting value).",
        "Помощник предложил неизвестное действие «delete_everything». Переформулируйте запрос.",
        "Какую заявку отменить?",
    ]
    assert run([], text="Что именно случилось?").clarifications == ["Что именно случилось?"]


def test_prompt_carries_now_engineers_and_assignments():
    ctx = context()
    session = named_session(ctx)
    system, user = build_messages("Арташкин заболел после обеда", session)
    assert "Текущее время плана: 00:00." in system["content"]
    assert "«после обеда» 14:00" in system["content"]
    assert '"name": "Бригада Арташкин"' in user["content"]
    assert '"address": "Город Москва, ул.Дубининская, д. 59 к 2"' in user["content"]
    assert '"planned_start": "' in user["content"] and user["content"].endswith(
        "Арташкин заболел после обеда"
    )


def test_prompt_and_nothing_found_hint_mention_transport_change():
    system, user = build_messages("Арташкин пересел на велосипед", named_session(context()))
    assert "смена транспорта" in system["content"]
    assert "propose_engineer_transport_change" in system["content"]
    assert '"transport": "car"' in user["content"]
    assert "смена транспорта" in NOTHING_FOUND
