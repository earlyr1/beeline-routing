"""Сетка окон визита: сама сетка, её выдача клиенту, отказ сервера от окна вне сетки и окно помощника на слоте.

Правило одно: окно, которое ВЫБРАЛ диспетчер, — слот сетки. Заявки данных сеткой не проверяются, и окно аварии
«как можно скорее» на весь день остаётся как есть.
"""

from pathlib import Path

import pytest

from app.domain.windows import build_slots, is_slot, slot_for, slots_text
from app.ingest.geocode import GeoResult
from app.llm.client import LlmResult, ToolCall
from app.llm.interpret import interpret
from app.llm.prompt import system_rules
from app.settings import BACKEND_DIR
from app.synth.config import SynthConfig
from tests.api_helpers import make_client, sample_bundle, upload
from tests.helpers import req
from tests.llm_helpers import ScriptedProvider, completion, ids, named_session, tool_call
from tests.planning_helpers import context, day_engineers, day_requests, new_session
from tests.timeline_helpers import fcfs_solves

CONFIG = Path(BACKEND_DIR) / "config" / "synth_config.yaml"
# Окна 205 заявок трёх реальных регионов выгрузки: ровно рабочий день 10:00–22:00 по два часа.
REAL_WINDOWS = [
    ("10:00", "12:00"),
    ("12:00", "14:00"),
    ("14:00", "16:00"),
    ("16:00", "18:00"),
    ("18:00", "20:00"),
    ("20:00", "22:00"),
]


@pytest.fixture(scope="module")
def cfg():
    return SynthConfig.load(CONFIG)


@pytest.fixture(scope="module")
def grid(cfg):
    return cfg.window_grid


def test_the_grid_is_the_working_day_cut_into_windows_of_one_length(cfg, grid):
    """Сетка конфига — окна настоящих данных, и она собрана из смены и длины окна, а не выписана отдельно."""
    assert [
        (slot["start"], slot["end"]) for slot in (s.model_dump(mode="json") for s in grid)
    ] == REAL_WINDOWS
    assert slots_text(grid) == "10:00–12:00, 12:00–14:00, 14:00–16:00, 16:00–18:00, 18:00–20:00, 20:00–22:00"
    day_start = min(shift.start for shift in cfg.shifts)
    day_end = max(shift.end for shift in cfg.shifts)
    assert (grid[0].start, grid[-1].end) == (day_start, day_end)
    assert all(slot.end - slot.start == cfg.urgent_event.window_min for slot in grid)
    # Сетка не может разойтись со сменой: смена сдвинулась — сдвинулись и слоты.
    later = cfg.model_copy(
        update={"shifts": [shift.model_copy(update={"start": 480}) for shift in cfg.shifts]}
    )
    assert later.window_grid[0].start == 480


def test_a_slot_length_that_does_not_divide_the_day_leaves_the_tail_out():
    """Хвост короче слота в сетку не попадает: клиенту называют окно полной длины, а не огрызок."""
    slots = build_slots(600, 1320, 50)
    assert (len(slots), slots[0].start, slots[-1].end) == (14, 600, 600 + 14 * 50)
    assert slots[-1].end < 1320
    # Час на сутки — ровно 24 слота; ноль и меньше длины у окна не бывает.
    assert len(build_slots(0, 24 * 60, 60)) == 24
    assert build_slots(600, 660, 120) == []
    with pytest.raises(ValueError, match="больше нуля"):
        build_slots(600, 1320, 0)


def test_the_slot_of_a_minute_takes_the_boundary_forward_and_falls_back_to_the_nearest(grid):
    assert (slot_for(grid, 600).start, slot_for(grid, 719).start) == (600, 600)
    # Граница достаётся следующему слоту: 12:00 — начало 12:00–14:00, а не конец 10:00–12:00.
    assert slot_for(grid, 720).start == 720
    # Время вне рабочего дня своего слота не имеет: называют ближайший.
    assert (slot_for(grid, 540).start, slot_for(grid, 1320).start, slot_for(grid, 1439).start) == (
        600,
        1200,
        1200,
    )
    assert slot_for((), 700) is None
    assert is_slot(grid, 720, 840) and not is_slot(grid, 690, 810)


def _ready(client, bundle=None):
    payload = (bundle or sample_bundle()).model_dump_json().encode()
    dataset_id = upload(client, "bundle.json", payload)
    assert client.get(f"/api/datasets/{dataset_id}").json()["status"] == "ready"
    return f"/api/datasets/{dataset_id}"


def _urgent(window_start, window_end, **extra):
    return {
        "type": "urgent",
        "time": "13:00",
        "request": {
            "id": "U1",
            "address": "Город Москва, ул.Таганская, д. 1",
            "lat": 55.751,
            "lon": 37.61,
            "duration_min": 30,
            "window_start": window_start,
            "window_end": window_end,
            "skill": "local",
            **extra,
        },
    }


OFF_GRID_TEXT = (
    "Окно заявки URG-U1 11:30–13:30 не из сетки окон: клиенту называют слот. "
    "Выберите один из: 10:00–12:00, 12:00–14:00, 14:00–16:00, 16:00–18:00, 18:00–20:00, 20:00–22:00."
)


@pytest.mark.parametrize("path", ["/events", "/timeline/events"])
def test_the_server_refuses_an_urgent_request_with_a_window_outside_the_grid(tmp_path, monkeypatch, path):
    """Что бы ни прислал клиент, произвольный интервал сервер не принимает: диспетчер называет клиенту слот."""
    fcfs_solves(monkeypatch)
    client, deps = make_client(tmp_path)
    deps.run_background = lambda task: None
    base = _ready(client)

    refused = client.post(f"{base}{path}", json=_urgent("11:30", "13:30"))

    assert refused.status_code == 422
    assert refused.json()["detail"] == OFF_GRID_TEXT
    # Отклонённое событие на шкале не остаётся.
    assert client.get(f"{base}/state").json()["timeline"] == []

    accepted = client.post(f"{base}{path}", json=_urgent("14:00", "16:00"))
    assert accepted.status_code == 200, accepted.text


def test_an_urgent_request_as_soon_as_possible_is_not_a_slot_and_goes_through(tmp_path, monkeypatch):
    """«Как можно скорее» — не слот: окно задаёт сервер, от времени события до конца смен, и сетка его не трогает."""
    fcfs_solves(monkeypatch)
    client, deps = make_client(tmp_path)
    deps.run_background = lambda task: None
    base = _ready(client)

    response = client.post(f"{base}/events", json=_urgent("11:30", "13:30", asap=True))

    assert response.status_code == 200, response.text
    stored = next(item for item in response.json()["requests"] if item["id"] == "U1")
    assert (stored["asap"], stored["window_start"], stored["window_end"]) == (True, "13:00", "18:00")


def test_a_request_of_the_data_keeps_its_window_and_stays_editable(tmp_path, monkeypatch):
    """Заявка ДАННЫХ сеткой не проверяется: окно на весь день грузится, планируется и правится дальше.

    Так живут аварии выгрузки (00:01–23:59): их окно — источник правды, а не выбор диспетчера. Сетка вступает
    в дело, только когда диспетчер окно МЕНЯЕТ.
    """
    fcfs_solves(monkeypatch)
    emergency = req("R4", 1, 1, "00:01", "23:59")
    base_bundle = sample_bundle()
    whole_day = base_bundle.model_copy(update={"requests": [*base_bundle.requests, emergency]})
    client, deps = make_client(tmp_path, bundle=whole_day)
    deps.run_background = lambda task: None
    base = _ready(client, whole_day)
    stored = next(item for item in client.get(f"{base}/state").json()["requests"] if item["id"] == "R4")
    assert (stored["window_start"], stored["window_end"]) == ("00:01", "23:59")

    # Длительность меняется, окно данных остаётся своё — сервер принимает.
    kept = client.post(
        f"{base}/events",
        json={
            "type": "request_updated",
            "time": "13:00",
            "request_id": "R4",
            "request": {**stored, "duration_min": 40},
        },
    )
    assert kept.status_code == 200, kept.text

    # А новое окно уже выбирает диспетчер, и оно должно быть слотом.
    refused = client.post(
        f"{base}/events",
        json={
            "type": "request_updated",
            "time": "13:00",
            "request_id": "R4",
            "request": {**stored, "window_start": "13:30", "window_end": "15:30"},
        },
    )
    assert refused.status_code == 422
    assert "не из сетки окон" in refused.json()["detail"]


def test_turning_off_as_soon_as_possible_asks_the_dispatcher_for_a_slot(tmp_path, monkeypatch):
    """Снять «как можно скорее» можно только слотом: окно такой заявки задал сервер, и клиенту его не называли.

    Диалог правки показывает окно самой заявки, а у «как можно скорее» это 13:00–18:00 от сервера. Сохранить его
    как обычное окно значит пообещать клиенту интервал, которого в сетке нет, поэтому одного снятия галочки мало.
    """
    fcfs_solves(monkeypatch)
    client, deps = make_client(tmp_path)
    deps.run_background = lambda task: None
    base = _ready(client)

    created = client.post(f"{base}/events", json=_urgent("11:30", "13:30", asap=True))
    assert created.status_code == 200, created.text
    stored = next(item for item in created.json()["requests"] if item["id"] == "U1")
    assert (stored["asap"], stored["window_start"], stored["window_end"]) == (True, "13:00", "18:00")

    def edit(request):
        return client.post(
            f"{base}/events",
            json={"type": "request_updated", "time": "13:00", "request_id": "U1", "request": request},
        )

    refused = edit({**stored, "asap": False})
    assert refused.status_code == 422
    assert "Окно заявки U1 13:00–18:00 не из сетки окон" in refused.json()["detail"]

    chosen = edit({**stored, "asap": False, "window_start": "14:00", "window_end": "16:00"})
    assert chosen.status_code == 200, chosen.text
    now = next(item for item in chosen.json()["requests"] if item["id"] == "U1")
    assert (now["asap"], now["window_start"], now["window_end"]) == (False, "14:00", "16:00")


def test_approving_a_proposal_is_checked_by_the_grid_too(tmp_path, monkeypatch):
    """Подтверждение предложения — такой же выбор окна: мимо сетки план не меняется и через чат.

    Черновик здесь собран без сетки в контексте помощника, поэтому окно доходит до подтверждения как названо:
    так видно, что за правило отвечает не только снапинг в interpret, но и сам шаг применения.
    """
    fcfs_solves(monkeypatch)
    client, deps = make_client(tmp_path)
    deps.run_background = lambda task: None
    deps.ingest.planning.window_grid = ()
    call = tool_call(
        "propose_request_update",
        {"request_id": "R1", "window_start": "11:30", "window_end": "13:30", "rationale": "Просили днём"},
    )
    deps.llm = ScriptedProvider(completion(tool_calls=[call])).client()
    base = _ready(client)

    [proposal] = client.post(f"{base}/chat", json={"text": "R1 перенести на 11:30–13:30"}).json()["proposals"]
    assert (proposal["event"]["request"]["window_start"], proposal["status"]) == ("11:30", "pending")

    approved = client.post(f"{base}/proposals/{proposal['id']}/approve")
    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert result["proposal"]["status"] == "failed"
    assert "не из сетки окон" in result["proposal"]["error"]
    # План не тронут: у заявки прежнее окно, и шкала пустая.
    stored = next(item for item in result["state"]["requests"] if item["id"] == "R1")
    assert (stored["window_start"], stored["window_end"]) == ("10:00", "12:00")
    assert result["state"]["timeline"] == []


def _drafts(calls, ctx, session=None):
    session = session or new_session(ctx=ctx, engineers=day_engineers())
    return interpret(LlmResult(calls=calls), session, ctx, ids()).drafts


def test_the_assistant_puts_the_window_it_named_on_a_slot(grid):
    """Окно, которое назвала модель, ложится на слот ещё до сборки события: клиенту называют слот."""
    ctx = context(
        window_grid=grid, geocode=lambda address, district: GeoResult(55.75, 37.61, "house", address)
    )
    arguments = {
        "address": "Город Москва, ул.Таганская, д. 1",
        "window_start": "13:20",
        "window_end": "16:40",
        "duration_min": 30,
        "skill": "local",
        "time": "12:30",
        "rationale": "Просили после обеда",
    }
    [draft] = _drafts([ToolCall("propose_urgent_request", arguments)], ctx)
    assert (draft.error, draft.event.request.window_start, draft.event.request.window_end) == (None, 720, 840)

    # Изменение заявки — то же правило, и заявка «как можно скорее» им не задета: её окно задаёт сервер.
    session = named_session(ctx)
    [moved] = _drafts(
        [
            ToolCall(
                "propose_request_update", {"request_id": "R2", "window_start": "16:30", "rationale": "Позже"}
            )
        ],
        ctx,
        session,
    )
    assert (moved.event.request.window_start, moved.event.request.window_end) == (960, 1080)
    # «Как можно скорее» сетка не трогает: окно такой заявки задаёт сервер, слотом оно не бывает.
    [asap] = _drafts(
        [ToolCall("propose_request_update", {"request_id": "R2", "asap": True, "rationale": "Скорее"})],
        ctx,
        session,
    )
    assert (asap.error, asap.event.request.asap) == (None, True)


def test_the_assistant_puts_a_request_that_stops_being_as_soon_as_possible_on_a_slot(grid):
    """«Уже не срочно» — тоже выбор окна: окно «как можно скорее» задал сервер, клиенту его не называли."""
    ctx = context(window_grid=grid)
    requests = day_requests()
    # R2 ждёт как можно скорее с 13:00: такое окно сервер ставит от времени события до конца смен.
    requests[1] = requests[1].model_copy(update={"asap": True, "window_start": 780, "window_end": 1080})
    session = new_session(ctx=ctx, requests=requests, engineers=day_engineers())

    [draft] = _drafts(
        [ToolCall("propose_request_update", {"request_id": "R2", "asap": False, "rationale": "Не горит"})],
        ctx,
        session,
    )

    assert draft.error is None
    assert (draft.event.request.asap, draft.event.request.window_start, draft.event.request.window_end) == (
        False,
        720,
        840,
    )
    # Что помощник взял окно с сетки сам, диспетчер видит в карточке предложения.
    assert "окно 12:00–14:00 с сетки" in draft.rationale


def test_the_assistant_says_where_it_moved_the_window_and_asks_about_time_outside_the_day(grid):
    """Сдвиг на слот виден диспетчеру, а время вне рабочего дня помощник не подменяет молча, а переспрашивает."""
    ctx = context(
        window_grid=grid, geocode=lambda address, district: GeoResult(55.75, 37.61, "house", address)
    )
    arguments = {
        "address": "Город Москва, ул.Таганская, д. 1",
        "window_start": "13:20",
        "window_end": "16:40",
        "duration_min": 30,
        "skill": "local",
        "time": "12:30",
        "rationale": "Просили после обеда",
    }
    [draft] = _drafts([ToolCall("propose_urgent_request", arguments)], ctx)
    assert draft.rationale == "Просили после обеда Окно 13:20–16:40 положено на слот 12:00–14:00."

    # Окно за рабочим днём ближайшим слотом не подменяется: он значил бы совсем другое время.
    night = {**arguments, "window_start": "22:00", "window_end": "23:00"}
    out = interpret(
        LlmResult(calls=[ToolCall("propose_urgent_request", night)]),
        new_session(ctx=ctx, engineers=day_engineers()),
        ctx,
        ids(),
    )
    assert out.drafts == []
    assert out.clarifications == [
        "Окна 22:00–23:00 в сетке нет: клиенту называют слот. Выберите один из: "
        "10:00–12:00, 12:00–14:00, 14:00–16:00, 16:00–18:00, 18:00–20:00, 20:00–22:00."
    ]


def test_the_assistant_is_told_the_grid_exists(grid):
    rules = system_rules("13:00", grid)
    assert "10:00–12:00, 12:00–14:00, 14:00–16:00, 16:00–18:00, 18:00–20:00, 20:00–22:00" in rules
    assert "слотом сетки" in rules
    # Без сетки правило про слоты не выдумывается: окно остаётся таким, как его назвали.
    assert "слотом сетки" not in system_rules("13:00")


def test_a_slot_the_assistant_named_is_left_alone_and_without_a_grid_nothing_moves(grid):
    """Слот сетка не двигает, а без сетки (её задаёт конфиг) окно уходит таким, как его назвали."""
    arguments = {
        "address": "Город Москва, ул.Таганская, д. 1",
        "window_start": "16:00",
        "window_end": "18:00",
        "duration_min": 30,
        "skill": "local",
        "time": "12:30",
        "rationale": "Назвали окно",
    }
    geocode = lambda address, district: GeoResult(55.75, 37.61, "house", address)  # noqa: E731
    [on_slot] = _drafts(
        [ToolCall("propose_urgent_request", arguments)], context(window_grid=grid, geocode=geocode)
    )
    assert (on_slot.event.request.window_start, on_slot.event.request.window_end) == (960, 1080)

    odd = {**arguments, "window_start": "13:20", "window_end": "16:40"}
    [as_named] = _drafts([ToolCall("propose_urgent_request", odd)], context(geocode=geocode))
    assert (as_named.event.request.window_start, as_named.event.request.window_end) == (800, 1000)
