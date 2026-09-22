"""Типы работ срочной заявки: нормативы организаторов в GET /api/config и заявка из диалога, которую принимает сервер.

frontend/src/test/urgentWorkTypes.golden.json — общий эталон backend и фронтенда. work_types в нём — ответ сервера:
тесты фронтенда берут цифры оттуда и не расходятся с сервером. events — события, которые собирает диалог срочной
заявки из каждого типа (frontend/src/lib/events.test.ts сверяет их с buildUrgentEvent); здесь они уходят в сервер.
Поменялись нормативы в config/synth_config.yaml — обновите work_types эталона по ответу /api/config, а events —
по сообщению теста фронтенда.
"""

import json
from pathlib import Path

import pytest
import yaml

from app.api.schemas import ClientConfig
from app.domain.enums import RequestTier, Skill, Transport
from app.ingest.beeline_csv import RawFile, RawRequestRow
from app.ingest.geocode import GeoResult
from app.settings import REPO_ROOT
from app.synth.config import SynthConfig
from app.synth.requests import build_requests
from app.synth.work_types import URGENT_WORK_TYPES, urgent_work_types
from tests.api_helpers import make_client, sample_bundle, upload
from tests.timeline_helpers import fcfs_solves

CONFIG = Path(__file__).resolve().parents[1] / "config" / "synth_config.yaml"
GOLDEN = REPO_ROOT / "frontend" / "src" / "test" / "urgentWorkTypes.golden.json"


@pytest.fixture(scope="module")
def cfg():
    return SynthConfig.load(CONFIG)


@pytest.fixture(scope="module")
def golden():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_work_types_carry_the_organisers_norms(cfg):
    """Четыре строки таблицы нормативов организаторов: навык, уровень, минуты работ, транспорт, оборудование,
    «как можно скорее»."""
    types = urgent_work_types(cfg)
    assert [
        (
            t.title,
            t.source_type_bk,
            t.skill,
            t.tier,
            t.duration_min,
            t.transport_required,
            t.needs_equipment,
            t.asap,
        )
        for t in types
    ] == [
        (
            "Авария",
            "Глобальная проблема",
            Skill.EMERGENCY,
            RequestTier.EMERGENCY,
            80,
            Transport.CAR,
            False,
            True,
        ),
        ("Подключение", "Подключение", Skill.CONNECTION, RequestTier.CONNECTION, 70, None, True, False),
        ("Ремонт у клиента", "Локальная заявка", Skill.LOCAL, RequestTier.ROUTINE, 30, None, False, False),
        (
            "Дозаказ оборудования",
            "Дозаказ",
            Skill.CONNECTION,
            RequestTier.ROUTINE,
            20,
            Transport.CAR,
            True,
            False,
        ),
    ]
    # Цифры — ровно таблицы yaml, а не их копия.
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    for work_type in types:
        assert work_type.skill == raw["skill_by_bk"][work_type.source_type_bk]
        assert work_type.tier == raw["tier_by_bk"][work_type.source_type_bk]
        assert work_type.duration_min == raw["duration_by_bk"][work_type.source_type_bk]
        assert work_type.asap == (work_type.source_type_bk in raw["urgent_bk_types"])


def _geo(address: str, district: str) -> GeoResult:
    return GeoResult(55.75, 37.6, "house", address)


def test_work_type_fills_what_the_bundle_gets_for_the_same_type(cfg):
    """Тип работ заполняет форму теми же правилами, по которым собирается заявка дня того же типа BK и HD."""
    rows = [
        RawRequestRow(
            row_index=index,
            request_id=str(index + 1),
            type_bk=work_type.source_type_bk,
            type_hd=work_type.source_type_hd,
            window_start=600,
            window_end=720,
            district="Таганский",
            address="Город Москва, ул.Тестовая, д. 1",
        )
        for index, work_type in enumerate(urgent_work_types(cfg))
    ]
    built = build_requests(cfg, RawFile(rows=rows, office_address="x", is_control=False), _geo)
    assert [
        (t.skill, t.tier, t.duration_min, t.transport_required, t.needs_equipment)
        for t in urgent_work_types(cfg)
    ] == [(r.skill, r.tier, r.duration_min, r.transport_required, r.needs_equipment) for r in built]


def test_a_config_without_a_work_type_leaves_it_out(cfg):
    without_extra = cfg.model_copy(
        update={"skill_by_bk": {k: v for k, v in cfg.skill_by_bk.items() if k != "Дозаказ"}}
    )
    assert [t.title for t in urgent_work_types(without_extra)] == [
        "Авария",
        "Подключение",
        "Ремонт у клиента",
    ]
    assert urgent_work_types(cfg.model_copy(update={"skill_by_bk": {}})) == []
    # Ответ без типов работ читается как раньше: поле пустое.
    assert ClientConfig(yandex_maps_api_key=None, llm_enabled=False, osrm_available=False).work_types == []
    assert len(URGENT_WORK_TYPES) == len(urgent_work_types(cfg))


def test_config_endpoint_serves_the_work_types_of_the_shared_golden(tmp_path, golden):
    # Дня этот тест не заводит: хранилищу тут нечего хранить, поэтому клиент один и на памяти.
    client, deps = make_client(tmp_path)
    served = client.get("/api/config").json()["work_types"]
    assert served == [t.model_dump(mode="json") for t in urgent_work_types(deps.ingest.synth_config)]
    assert served == golden["work_types"]


@pytest.mark.parametrize("index", range(len(URGENT_WORK_TYPES)))
def test_urgent_request_built_by_the_dialog_is_accepted_for_every_work_type(api, monkeypatch, golden, index):
    """Событие диалога уходит на шкалу, как это делает фронтенд: часы на времени события, затем выбор варианта."""
    fcfs_solves(monkeypatch)
    client, deps = api()
    deps.run_background = lambda task: None
    base = f"/api/datasets/{upload(client, 'bundle.json', sample_bundle().model_dump_json().encode())}"
    assert client.get(base).json()["status"] == "ready"
    event = golden["events"][index]
    work_type = golden["work_types"][index]
    assert client.post(f"{base}/cursor", json={"time": event["time"]}).status_code == 200

    response = client.post(f"{base}/timeline/events", json=event)

    assert response.status_code == 200, response.text
    state = response.json()
    assert state["pending_choice"]["entry_id"] == "tl_1"
    chosen = client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "optimal"})
    assert chosen.status_code == 200, chosen.text
    state = chosen.json()
    assert [(item["status"], item["variant"]) for item in state["timeline"]] == [("applied", "optimal")]
    stored = next(request for request in state["requests"] if request["id"] == event["request"]["id"])
    fields = (
        "skill",
        "duration_min",
        "transport_required",
        "needs_equipment",
        "asap",
        "source_type_bk",
        "source_type_hd",
    )
    assert {key: stored[key] for key in fields} == {key: work_type[key] for key in fields}
    # Срочная заявка диспетчера любого типа работ — «Срочная», а уровень у неё — род работ её типа BK, как у заявки
    # дня: так её ставит сервер, так её шлёт клиент.
    assert (stored["priority"], stored["tier"], stored["status"]) == ("urgent", work_type["tier"], "active")
    assert stored["tier"] == event["request"]["tier"]
    if work_type["asap"]:
        # Окно аварии задаёт сервер: от времени события до конца смен инженеров дня (у тестовых бригад 18:00).
        assert (stored["window_start"], stored["window_end"]) == ("13:00", "18:00")
    else:
        assert (stored["window_start"], stored["window_end"]) == ("14:00", "16:00")
    planned = {visit["request_id"] for route in state["plan"]["routes"] for visit in route["visits"]}
    assert stored["id"] in planned


def test_server_sets_the_tier_of_an_urgent_request_by_its_bk_type_whatever_the_client_sends(
    api, monkeypatch, golden
):
    """Уровень срочной заявки ставит сервер по типу BK; заявка без типа из таблицы нормативов (старый диалог, чат) — авария."""
    fcfs_solves(monkeypatch)
    client, deps = api()
    deps.run_background = lambda task: None
    base = f"/api/datasets/{upload(client, 'bundle.json', sample_bundle().model_dump_json().encode())}"
    assert client.post(f"{base}/cursor", json={"time": "13:00"}).status_code == 200
    connection = golden["events"][1]
    sent = [
        {**connection, "request": {**connection["request"], "id": "URG-CONN", "tier": "emergency"}},
        {
            **connection,
            "request": {
                **connection["request"],
                "id": "URG-OLD",
                "tier": "routine",
                "source_type_bk": "Срочная заявка диспетчера",
                "source_type_hd": "",
            },
        },
    ]
    for index, event in enumerate(sent, start=1):
        response = client.post(f"{base}/timeline/events", json=event)
        assert response.status_code == 200, response.text
        chosen = client.put(f"{base}/timeline/events/tl_{index}/variant", json={"variant": "optimal"})
        assert chosen.status_code == 200, chosen.text

    stored = {request["id"]: request for request in chosen.json()["requests"]}
    assert (stored["URG-CONN"]["priority"], stored["URG-CONN"]["tier"]) == ("urgent", "connection")
    assert (stored["URG-OLD"]["priority"], stored["URG-OLD"]["tier"]) == ("urgent", "emergency")
