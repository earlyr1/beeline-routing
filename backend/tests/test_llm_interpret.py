from typing import Any

from app.domain.enums import EventType, Priority, Skill, Transport
from app.domain.models import Event
from app.domain.timeutil import fmt_hhmm
from app.ingest.geocode import GeoResult
from app.llm.client import LlmResult, ToolCall
from app.llm.interpret import (
    NOTHING_FOUND,
    RESTORE_UNSUPPORTED,
    REWRITE_HINT,
    URGENT_WINDOW_MISSING,
    interpret,
    not_understood_text,
    reply_text,
)
from app.llm.prompt import build_messages
from app.llm.tools import TOOL_NAMES
from app.planning.session import apply_event
from tests.helpers import req
from tests.llm_helpers import ids, named_session
from tests.planning_helpers import context, day_requests, new_session


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
    assert out.not_understood == []
    assert [(d.event.type, d.event.request_id, d.event.time, d.error) for d in out.drafts] == [
        (EventType.CANCEL, "R1", 540, None),
        (EventType.CANCEL, "R2", 780, None),
    ]
    assert out.drafts[0].rationale == "Клиент отказался"


def test_engineer_resolved_by_surname_and_time_defaults_to_now():
    out = run([ToolCall("propose_engineer_unavailable", {"engineer_id": "белузин", "rationale": "Заболел"})])
    draft = out.drafts[0]
    assert (draft.event.engineer_id, draft.event.time, draft.error) == ("E2", 0, None)


def test_ambiguous_or_unknown_names_are_not_understood():
    out = run(
        [
            ToolCall("propose_engineer_unavailable", {"engineer_id": "Бригада", "rationale": "?"}),
            ToolCall("propose_cancel", {"request_id": "Тверская", "rationale": "?"}),
        ]
    )
    assert out.drafts == []
    assert out.not_understood == [
        "под «Бригада» подходят несколько инженеров: Бригада Арташкин, Бригада Белузин",
        "заявка «Тверская» не найдена",
    ]


def test_conflicts_with_day_state_become_failed_drafts():
    ctx = context()
    session = apply_event(
        named_session(ctx), Event(type=EventType.CANCEL, time="12:00", request_id="R2"), ctx
    )
    out = run(
        [ToolCall("propose_cancel", {"request_id": "R2", "time": "13:00", "rationale": "Отказ"})],
        ctx=ctx,
        session=session,
    )
    assert out.drafts[0].error == "Заявка R2 уже отменена."


def test_restoring_a_cancelled_request_is_not_offered_and_is_answered_honestly():
    # Инструмента возврата у помощника нет, а правила говорят модели ничего не вызывать: ответит сервис.
    assert "propose_restore" not in TOOL_NAMES
    system, _ = build_messages("Верните заявку R2", named_session(context()))
    assert "возврат отменённой заявки не поддерживается" in system["content"]
    assert "propose_restore" not in system["content"]
    # Модель со старой памятью назвала прежний инструмент: предложения нет, есть честный ответ.
    out = run([ToolCall("propose_restore", {"request_id": "R2", "rationale": "Снова в силе"})])
    assert out.drafts == []
    assert out.notices == [RESTORE_UNSUPPORTED] and out.not_understood == []
    assert reply_text(out, "Верните заявку R2") == RESTORE_UNSUPPORTED
    assert "возврат" not in NOTHING_FOUND


def test_a_restore_request_with_no_answer_from_the_model_gets_the_honest_refusal():
    # Модель промолчала или ответила текстом вместо инструментов: просьба вернуть заявку получает отказ, а не «Не понял».
    for text in ("вернуть заявку 57866", "Верните 57866, клиент передумал", "57866 снова в силе"):
        assert reply_text(run([]), text) == RESTORE_UNSUPPORTED
        assert reply_text(run([], text="Возврат не поддерживается."), text) == RESTORE_UNSUPPORTED
    for text in ("Как дела?", "Всё верно", "Вернулся из отпуска"):
        assert reply_text(run([]), text) == f"Не понял: {NOTHING_FOUND}. {REWRITE_HINT}"


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
                {"engineer_id": "белузин", "transport": "public", "rationale": "Дальше пешком"},
            ),
        ]
    )
    assert out.not_understood == []
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
        (EventType.ENGINEER_TRANSPORT_CHANGED, "E2", Transport.CAR, Transport.PUBLIC, 0, None),
    ]
    assert out.drafts[0].rationale == "Сломалась машина, пересел на велосипед"


def test_transport_change_for_same_engineer_and_time_keeps_different_transports():
    arguments = {"engineer_id": "E1", "time": "13:00", "rationale": "Пересел"}
    out = run(
        [
            ToolCall("propose_engineer_transport_change", {**arguments, "transport": "bike"}),
            ToolCall("propose_engineer_transport_change", {**arguments, "transport": "public"}),
            ToolCall("propose_engineer_transport_change", {**arguments, "transport": "bike"}),
        ]
    )
    assert [d.event.transport for d in out.drafts] == [Transport.BIKE, Transport.PUBLIC]


def test_old_foot_and_old_names_from_the_model_mean_public_transport():
    arguments = {"time": "13:00", "rationale": "Дальше пешком"}
    names = [
        "foot",
        "Пешеход",
        "пешком",
        "Общественный транспорт",
        "общественный транспорт и пешком",
        "public",
    ]
    for name in names:
        out = run(
            [
                ToolCall(
                    "propose_engineer_transport_change", {**arguments, "engineer_id": "E1", "transport": name}
                )
            ]
        )
        assert out.not_understood == [], name
        assert [d.event.transport for d in out.drafts] == [Transport.PUBLIC], name


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
    assert out.not_understood == [
        "не удалось разобрать предложение «propose_engineer_transport_change»: transport: неизвестный тип "
        "транспорта «plane», допустимы car, bike, public",
        "инженер «Кузнецов» не найден",
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
    # Прежний «foot» из ответа модели — общественный транспорт и пешком.
    foot = run([ToolCall("propose_urgent_request", {**arguments, "transport_required": "foot"})], ctx=ctx)
    assert foot.drafts[0].event.request.transport_required == Transport.PUBLIC


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
            ToolCall("not_understood", {"reason": "не сказано, какую заявку отменить"}),
            ToolCall("propose_cancel", {"request_id": "R3", "time": "13:00", "rationale": "Отмена"}),
            ToolCall("propose_cancel", {"request_id": "R3", "time": "13:00", "rationale": "Отмена ещё раз"}),
        ]
    )
    assert len(out.drafts) == 1
    assert out.not_understood[0].startswith("не удалось разобрать предложение «propose_urgent_request»")
    assert out.not_understood[1:] == [
        "не удалось разобрать предложение «propose_cancel»: аргументы не являются JSON (Expecting value)",
        "помощник предложил неизвестное действие «delete_everything»",
        "не сказано, какую заявку отменить",
    ]
    # Текст вместо инструментов — не причина сам по себе: он идёт в «Не понял», только если модель больше ничего не дала.
    prose = run([], text="Что именно случилось?")
    assert (prose.not_understood, prose.prose) == ([], "Что именно случилось?")
    assert reply_text(prose, "Так себе день") == f"Не понял: Что именно случилось. {REWRITE_HINT}"


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


def test_request_update_by_street_merges_new_window_into_current_request():
    ctx = context()
    session = named_session(ctx)
    before = session.request("R2")
    arguments = {
        "request_id": "Дубининская",
        "window_start": "15:00",
        "window_end": "17:00",
        "time": "13:00",
        "rationale": "Клиент просит приехать позже",
    }
    out = run([ToolCall("propose_request_update", arguments)], ctx=ctx, session=session)
    assert out.not_understood == []
    [draft] = out.drafts
    assert (draft.event.type, draft.event.request_id, draft.event.time, draft.error) == (
        EventType.REQUEST_UPDATED,
        "R2",
        780,
        None,
    )
    assert draft.event.request == before.model_copy(update={"window_start": 900, "window_end": 1020})
    assert draft.event.previous_request == before
    assert draft.rationale == "Клиент просит приехать позже"


def test_request_update_changes_address_skill_priority_and_transport():
    geocoded = []

    def geocode(address, district):
        geocoded.append(address)
        return GeoResult(55.76, 37.62, "street", address)

    ctx = context(geocode=geocode)
    requests = day_requests()
    requests[2] = requests[2].model_copy(update={"transport_required": Transport.CAR})
    session = new_session(ctx=ctx, requests=requests)
    arguments = {
        "request_id": "R3",
        "address": " Москва, ул. Новая, 5 ",
        "duration_min": 50,
        "skill": "emergency",
        "priority": "urgent",
        "transport_required": "none",
        "rationale": "Авария по новому адресу",
    }
    out = run([ToolCall("propose_request_update", arguments)], ctx=ctx, session=session)
    [draft] = out.drafts
    assert draft.error is None and geocoded == ["Москва, ул. Новая, 5"]
    assert draft.event.request == session.request("R3").model_copy(
        update={
            "address": "Москва, ул. Новая, 5",
            "lat": 55.76,
            "lon": 37.62,
            "geocode_precision": "street",
            "duration_min": 50,
            "skill": Skill.EMERGENCY,
            "priority": Priority.URGENT,
            "transport_required": None,
        }
    )
    assert draft.event.time == 0


def test_request_update_without_fields_conflicts_and_unknown_address():
    ctx = context(geocode=lambda address, district: GeoResult(None, None, "none", None))
    session = named_session(ctx)
    started = next(v for route in session.plan.routes for v in route.visits if v.request_id == "R1")
    assert started.start < 780
    out = run(
        [
            ToolCall(
                "propose_request_update", {"request_id": "Дубининская", "time": "13:00", "rationale": "?"}
            ),
            ToolCall(
                "propose_request_update",
                {"request_id": "R1", "duration_min": 90, "time": "13:00", "rationale": "Дольше"},
            ),
            ToolCall(
                "propose_request_update",
                {"request_id": "R3", "window_start": "16:00", "window_end": "16:00", "rationale": "Позже"},
            ),
            ToolCall(
                "propose_request_update",
                {"request_id": "R3", "address": "Нигде, д. 1", "rationale": "Переезд"},
            ),
            ToolCall(
                "propose_request_update", {"request_id": "R3", "duration_min": 30, "rationale": "Как было"}
            ),
        ],
        ctx=ctx,
        session=session,
    )
    assert out.not_understood == [
        "не сказано, что изменить в заявке R2: окно, длительность, адрес или другое поле"
    ]
    assert [(d.event.request_id, d.error) for d in out.drafts] == [
        ("R1", f"Заявка R1 уже в работе с {fmt_hhmm(started.start)}, изменить её нельзя."),
        ("R3", "Конец окна заявки R3 должен быть позже начала."),
        ("R3", "Адрес «Нигде, д. 1» не найден на карте. Укажите точку на карте."),
        ("R3", "В заявке R3 ничего не изменилось."),
    ]


def test_separate_edits_of_one_request_are_not_merged_as_duplicates():
    arguments = {"request_id": "R3", "time": "13:00", "rationale": "Изменение"}
    out = run(
        [
            ToolCall("propose_request_update", {**arguments, "duration_min": 45}),
            ToolCall("propose_request_update", {**arguments, "duration_min": 60}),
            ToolCall("propose_request_update", {**arguments, "duration_min": 45}),
        ]
    )
    assert [d.event.request.duration_min for d in out.drafts] == [45, 60]


def test_prompt_and_nothing_found_hint_mention_request_update():
    system, user = build_messages(
        "Клиент на Дубининской просит перенести визит на вечер", named_session(context())
    )
    assert "изменение заявки" in system["content"]
    assert "propose_request_update" in system["content"]
    assert '"duration_min": 30' in user["content"]
    assert "изменение заявки" in NOTHING_FOUND


def test_request_update_with_inverted_window_is_failed_draft():
    ctx = context()
    session = named_session(ctx)
    stored = session.request("R3")
    out = run(
        [
            ToolCall(
                "propose_request_update",
                {
                    "request_id": "R3",
                    "window_start": "17:00",
                    "window_end": "16:00",
                    "duration_min": 45,
                    "time": "13:00",
                    "rationale": "Позже",
                },
            ),
            ToolCall(
                "propose_request_update",
                {"request_id": "R2", "window_end": "13:30", "rationale": "Раньше конца"},
            ),
        ],
        ctx=ctx,
        session=session,
    )
    assert out.not_understood == []
    inverted, before_start = out.drafts
    assert (inverted.event.type, inverted.event.request_id, inverted.event.time) == (
        EventType.REQUEST_UPDATED,
        "R3",
        780,
    )
    assert inverted.error == "Конец окна заявки R3 должен быть позже начала."
    # Окно с концом раньше начала нельзя записать в заявку: в черновике прежнее окно, остальные изменения видны.
    assert inverted.event.request == stored.model_copy(update={"duration_min": 45})
    assert inverted.rationale == "Позже"
    # Конец нового окна 13:30 раньше начала 14:00: тот же текст, а не ошибка разбора.
    assert (before_start.event.request_id, before_start.error) == (
        "R2",
        "Конец окна заявки R2 должен быть позже начала.",
    )


def test_engineer_delay_by_surname_becomes_pending_draft():
    out = run(
        [
            ToolCall(
                "propose_engineer_delay",
                {"engineer_id": "Белузин", "delay_min": 40, "time": "13:00", "rationale": "Застрял в пробке"},
            ),
            ToolCall(
                "propose_engineer_delay",
                {"engineer_id": "арташкин", "delay_min": "30", "rationale": "Опоздает"},
            ),
        ]
    )
    assert out.not_understood == []
    assert [
        (d.event.type, d.event.engineer_id, d.event.delay_min, d.event.time, d.error) for d in out.drafts
    ] == [
        (EventType.ENGINEER_DELAYED, "E2", 40, 780, None),
        (EventType.ENGINEER_DELAYED, "E1", 30, 0, None),
    ]
    assert out.drafts[0].rationale == "Застрял в пробке"


def test_engineer_delay_with_bad_delay_unknown_or_unavailable_engineer():
    ctx = context()
    session = apply_event(
        named_session(ctx), Event(type=EventType.ENGINEER_UNAVAILABLE, time="12:00", engineer_id="E1"), ctx
    )
    out = run(
        [
            ToolCall("propose_engineer_delay", {"engineer_id": "Белузин", "delay_min": 0, "rationale": "?"}),
            ToolCall(
                "propose_engineer_delay", {"engineer_id": "Кузнецов", "delay_min": 30, "rationale": "?"}
            ),
            ToolCall(
                "propose_engineer_delay",
                {"engineer_id": "Арташкин", "delay_min": 30, "time": "13:00", "rationale": "Пробка"},
            ),
        ],
        ctx=ctx,
        session=session,
    )
    assert out.not_understood == [
        "не удалось разобрать предложение «propose_engineer_delay»: delay_min: задержка должна быть от 5 до 480 минут",
        "инженер «Кузнецов» не найден",
    ]
    [draft] = out.drafts
    assert (draft.event.engineer_id, draft.event.delay_min) == ("E1", 30)
    assert draft.error == "Бригада Арташкин недоступен с 12:00, задержку поставить нельзя."


def test_delays_of_one_engineer_with_different_minutes_are_not_duplicates():
    arguments = {"engineer_id": "E1", "time": "13:00", "rationale": "Задержка"}
    out = run(
        [
            ToolCall("propose_engineer_delay", {**arguments, "delay_min": 20}),
            ToolCall("propose_engineer_delay", {**arguments, "delay_min": 40}),
            ToolCall("propose_engineer_delay", {**arguments, "delay_min": 20}),
        ]
    )
    assert [d.event.delay_min for d in out.drafts] == [20, 40]


def test_prompt_and_nothing_found_hint_mention_engineer_delay():
    system, _ = build_messages("Белузин застрял в пробке на полчаса", named_session(context()))
    assert "задержка инженера" in system["content"]
    assert "propose_engineer_delay" in system["content"]
    assert "задержка инженера" in NOTHING_FOUND


def test_prompt_and_nothing_found_hint_mention_transport_change():
    system, user = build_messages("Арташкин пересел на велосипед", named_session(context()))
    assert "смена транспорта" in system["content"]
    assert "propose_engineer_transport_change" in system["content"]
    assert '"transport": "car"' in user["content"]
    assert "смена транспорта" in NOTHING_FOUND


def urgent_day_session(ctx):
    """День со срочной заявкой Билайна 57866: во фронте она подписана «URG-57866», номер остаётся прежним."""
    urgent = req("57866", 1, 1, "13:00", "15:00", priority=Priority.URGENT)
    return new_session(ctx=ctx, requests=[*day_requests(), urgent])


def test_urgent_request_is_found_by_the_shown_urg_number():
    ctx = context()
    session = urgent_day_session(ctx)
    out = run(
        [
            ToolCall("propose_cancel", {"request_id": "URG-57866", "rationale": "Клиент отказался"}),
            ToolCall(
                "propose_cancel", {"request_id": " urg - 57866 ", "time": "13:00", "rationale": "Отмена"}
            ),
            ToolCall("propose_cancel", {"request_id": "URG-99999", "rationale": "Отмена"}),
        ],
        ctx=ctx,
        session=session,
    )
    assert out.not_understood == ["заявка «URG-99999» не найдена"]
    assert [(d.event.request_id, d.event.time, d.error) for d in out.drafts] == [
        ("57866", 0, None),
        ("57866", 780, None),
    ]


def test_urgent_request_from_chat_is_still_found_by_its_own_number():
    ctx = context()
    from_chat = req("URG-AI-001", 1, 1, "13:00", "15:00", priority=Priority.URGENT).model_copy(
        update={"address": "Город Москва, ул.Таганская, д. 3"}
    )
    session = new_session(ctx=ctx, requests=[*day_requests(), from_chat])
    out = run(
        [ToolCall("propose_cancel", {"request_id": "URG-AI-001", "rationale": "Авария устранена"})],
        ctx=ctx,
        session=session,
    )
    assert out.not_understood == []
    assert [(d.event.request_id, d.error) for d in out.drafts] == [("URG-AI-001", None)]


def test_assistant_answers_sign_an_urgent_request_of_the_day():
    ctx = context()
    session = urgent_day_session(ctx)
    out = run(
        [
            ToolCall("propose_request_update", {"request_id": "57866", "rationale": "Что-то поменять"}),
            ToolCall("propose_cancel", {"request_id": "адрес", "rationale": "Отмена"}),
            ToolCall(
                "propose_request_update",
                {"request_id": "57866", "window_start": "16:00", "window_end": "16:00", "rationale": "Окно"},
            ),
        ],
        ctx=ctx,
        session=session,
    )
    assert out.not_understood == [
        "не сказано, что изменить в заявке URG-57866: окно, длительность, адрес или другое поле",
        "под «адрес» подходят несколько заявок: R1 (адрес R1); R2 (адрес R2); R3 (адрес R3); "
        "URG-57866 (адрес 57866)",
    ]
    assert [(d.event.request_id, d.error) for d in out.drafts] == [
        ("57866", "Конец окна заявки URG-57866 должен быть позже начала.")
    ]


BELUZIN_LATE = "не сказано, на сколько задерживается Белузин"


def test_not_understood_becomes_a_single_not_understood_answer():
    """Вопросов помощник не задаёт: памяти между сообщениями нет, и диспетчер пишет сообщение целиком заново."""
    out = run([ToolCall("not_understood", {"reason": BELUZIN_LATE})])
    assert (out.drafts, out.not_understood, out.notices) == ([], [BELUZIN_LATE], [])
    assert reply_text(out, "Белузин опоздает") == (
        "Не понял: не сказано, на сколько задерживается Белузин. "
        "Напишите сообщение целиком ещё раз — прошлых сообщений помощник не помнит."
    )


def test_the_old_clarifying_tool_reads_as_not_understood():
    """Модель со старой памятью ещё зовёт ask_clarification: её вопрос — та же причина, что у not_understood."""
    expected = reply_text(run([ToolCall("not_understood", {"reason": BELUZIN_LATE})]), "Белузин опоздает")
    for name in ("ask_clarification", "not_understood"):
        for key in ("question", "reason"):
            out = run([ToolCall(name, {key: BELUZIN_LATE})])
            assert out.not_understood == [BELUZIN_LATE], (name, key)
            assert reply_text(out, "Белузин опоздает") == expected, (name, key)
    question = run([ToolCall("ask_clarification", {"question": "На сколько задерживается Белузин?"})])
    assert reply_text(question, "Белузин опоздает") == (
        f"Не понял: На сколько задерживается Белузин. {REWRITE_HINT}"
    )


def test_a_reason_from_the_model_gets_no_second_not_understood_and_no_question_mark():
    reasons: list[tuple[dict[str, Any], str]] = [
        ({"reason": "Не понял, кто заболел?"}, "кто заболел"),
        ({"reason": "не понял: не назван   инженер."}, "не назван инженер"),
        ({"reason": "  "}, "помощник не назвал, чего не хватает"),
        ({"reason": 42}, "помощник не назвал, чего не хватает"),
        ({}, "помощник не назвал, чего не хватает"),
    ]
    for arguments, reason in reasons:
        out = run([ToolCall("not_understood", arguments)])
        assert out.not_understood == [reason], arguments
        answer = reply_text(out, "Кто-то заболел")
        assert answer == f"Не понял: {reason}. {REWRITE_HINT}", arguments
        assert answer.count("Не понял") == 1 and "?" not in answer, arguments
    unparsed = run([ToolCall("not_understood", None, "аргументы не являются JSON (Expecting value)")])
    assert unparsed.not_understood == ["помощник не назвал, чего не хватает"]


def test_server_reasons_read_as_not_understood():
    """Причины сервиса — не вопросы: они встают в ту же фразу «Не понял: …»."""
    unknown = run(
        [ToolCall("propose_engineer_unavailable", {"engineer_id": "Кузнецов", "rationale": "Заболел"})]
    )
    assert (
        reply_text(unknown, "Кузнецов заболел") == f"Не понял: инженер «Кузнецов» не найден. {REWRITE_HINT}"
    )
    half_window = {
        "address": "Москва, ул. Тестовая, 1",
        "window_start": "14:00",
        "duration_min": 30,
        "skill": "local",
        "rationale": "Срочно",
    }
    urgent = run([ToolCall("propose_urgent_request", half_window)])
    assert reply_text(urgent, "Срочно на Тестовую с двух") == (
        "Не понял: у срочной заявки названа одна граница окна, а нужно окно целиком или «как можно скорее». "
        + REWRITE_HINT
    )
    assert URGENT_WINDOW_MISSING[0].islower() and not URGENT_WINDOW_MISSING.endswith(".")


def test_several_reasons_share_one_not_understood_and_proposals_stay():
    out = run(
        [
            ToolCall("propose_cancel", {"request_id": "R1", "rationale": "Отмена"}),
            ToolCall("propose_engineer_unavailable", {"engineer_id": "Кузнецов", "rationale": "Заболел"}),
            ToolCall("not_understood", {"reason": "не сказано, кто опоздает"}),
            ToolCall("propose_engineer_unavailable", {"engineer_id": "Кузнецов", "rationale": "Не выйдет"}),
        ]
    )
    assert len(out.drafts) == 1
    answer = reply_text(out, "Отмена по R1, Кузнецов заболел, кто-то опоздает")
    assert answer == (
        "Не понял:\n— инженер «Кузнецов» не найден;\n— не сказано, кто опоздает.\n" + REWRITE_HINT
    )
    assert answer.count("Не понял") == 1
    assert not_understood_text(["заявка «Тверская» не найдена"]) == (
        f"Не понял: заявка «Тверская» не найдена. {REWRITE_HINT}"
    )
    # Предложения есть, причин нет: сказать диспетчеру нечего.
    assert (
        reply_text(run([ToolCall("propose_cancel", {"request_id": "R1", "rationale": "Отмена"})]), "R1")
        is None
    )


def test_the_assistant_is_told_not_to_ask_questions():
    system, _ = build_messages("Белузин опоздает", named_session(context()))
    rules = system["content"]
    assert "ask_clarification" not in rules and "ask_clarification" not in TOOL_NAMES
    assert "not_understood" in TOOL_NAMES
    assert "Памяти между сообщениями нет" in rules and "вопросов не задавай" in rules
    [rule] = [line for line in rules.splitlines() if line.startswith("10. ")]
    assert "вызови not_understood и коротко, без вопроса назови, чего не хватает" in rule
