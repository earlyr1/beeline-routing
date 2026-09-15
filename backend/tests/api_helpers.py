import hashlib

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.deps import build_deps
from app.domain.models import Bundle
from app.ingest.bundle import save_bundle
from app.ingest.geocode import GeoHit
from app.settings import Settings
from tests.planning_helpers import OFFICE, day_engineers, day_requests

HEADER = "Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Гигабитное подключение\r\n"


class HashGeocoder:
    def lookup(self, query):
        digest = hashlib.sha256(query.encode()).digest()
        return GeoHit(55.74 + digest[0] / 255 * 0.02, 37.59 + digest[1] / 255 * 0.03, "building")


def sample_bundle():
    requests = [r.model_copy(update={"district": "Таганский"}) for r in day_requests()]
    return Bundle(region="t", office=OFFICE, requests=requests, engineers=day_engineers())


def make_client(tmp_path, bundle=None):
    bundle = bundle or sample_bundle()
    save_bundle(bundle, tmp_path / "bundles" / bundle.region / "bundle.json")
    settings = Settings(
        data_dir=tmp_path,
        cache_path=tmp_path / "cache.sqlite",
        osrm_url=None,
        yandex_maps_api_key="test-key",
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
        geocoder="cache-only",
        solver_time_limit_s=1,
    )
    deps = build_deps(settings, geocoder_override=HashGeocoder())
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
