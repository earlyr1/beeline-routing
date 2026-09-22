import hashlib

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.deps import build_deps
from app.domain.models import Bundle
from app.ingest.bundle import save_bundle
from app.ingest.geocode import GeoHit
from app.settings import Settings
from tests.planning_helpers import OFFICE, day_engineers, day_requests

HEADER = "Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Гигабитное подключение\r\n"

# Тест уровня API, которому база не нужна: дня он не заводит и в days не оставляет ни строки. Параметризация
# перекрывает фикстуру state_backend (tests/conftest.py) — тест идёт один раз, без половины [postgres],
# которая оплачивала бы пул соединений и миграцию ради ровно нуля запросов.
memory_only = pytest.mark.parametrize("state_backend", [None], ids=["memory"])


class HashGeocoder:
    def __init__(self, category="building"):
        self.category = category

    def lookup(self, query):
        digest = hashlib.sha256(query.encode()).digest()
        return GeoHit(55.74 + digest[0] / 255 * 0.02, 37.59 + digest[1] / 255 * 0.03, self.category)


def sample_bundle():
    requests = [r.model_copy(update={"district": "Таганский"}) for r in day_requests()]
    return Bundle(region="t", office=OFFICE, requests=requests, engineers=day_engineers())


def prepared_bundle(region, title):
    """Бандл подготовленного региона: регион и название офиса такие же, как у бандлов data/bundles."""
    bundle = sample_bundle()
    office = bundle.office.model_copy(update={"region": region, "title": title})
    return bundle.model_copy(update={"region": region, "office": office})


def make_client(tmp_path, bundle=None, geocoder=None, **limits):
    """limits переопределяет лимиты OR-Tools (solver_time_limit_s, solver_time_limit_lunch_s): по умолчанию
    1 секунда. Туда же идёт database_url — где живёт день; без него день живёт в памяти процесса.

    Тесты уровня API зовут эту функцию не сами, а через фикстуру `api` (tests/conftest.py): она подставляет
    хранилище и закрывает соединения после теста."""
    bundle = bundle or sample_bundle()
    save_bundle(bundle, tmp_path / "bundles" / bundle.region / "bundle.json")
    settings = Settings(
        data_dir=tmp_path,
        cache_path=tmp_path / "cache.sqlite",
        transit_dir=tmp_path / "transit",
        osrm_url=None,
        yandex_maps_api_key="test-key",
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
        geocoder="cache-only",
        **{"solver_time_limit_s": 1, "solver_time_limit_lunch_s": 1, **limits},
    )
    deps = build_deps(settings, geocoder_override=geocoder or HashGeocoder())
    return TestClient(create_app(deps)), deps


def csv_bytes(rows, office=OFFICE.address, encoding="cp1251"):
    lines = [HEADER]
    for request_id, start, end, address in rows:
        lines.append(
            f"{request_id};Локальная заявка;Нет линка;17.08.2026 {start};17.08.2026 {end};Таганский;"
            f"{address};Нет\r\n"
        )
    if office:
        lines.append(f"Адрес Офиса;{office};;;;;;\r\n")
    return "".join(lines).encode(encoding)


def upload(client, name, data):
    response = client.post("/api/upload", files={"file": (name, data, "application/octet-stream")})
    assert response.status_code == 202, response.text
    return response.json()["dataset_id"]
