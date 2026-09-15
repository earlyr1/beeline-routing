"""Фоновый предподсчёт загруженного файла: разбор, регион, геокодинг, матрица, первый план."""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.api.registry import DatasetRecord, PreparedDay
from app.api.schemas import GeocodingCounts, NotFoundAddress, UploadReport
from app.domain.models import Bundle, Request
from app.ingest.beeline_csv import RawFile, parse_beeline_csv
from app.ingest.bundle import load_bundle
from app.ingest.geocode import GeoResult
from app.planning.session import PlanningContext, start_session
from app.solvers.problem import make_problem
from app.synth.config import SynthConfig
from app.synth.requests import build_requests

GeocodeFn = Callable[[str, str], GeoResult]


class BundleStore:
    """Подготовленные бандлы регионов из data/bundles/<region>/bundle.json (читаются один раз)."""

    def __init__(self, bundles_dir: Path) -> None:
        self._dir = Path(bundles_dir)
        self._bundles: dict[str, Bundle] | None = None
        self._lock = threading.Lock()

    def all(self) -> dict[str, Bundle]:
        with self._lock:
            if self._bundles is None:
                self._bundles = {
                    path.parent.name: load_bundle(path) for path in sorted(self._dir.glob("*/bundle.json"))
                }
            return self._bundles


@dataclass
class IngestDeps:
    bundles: BundleStore
    synth_config: SynthConfig
    geocode: GeocodeFn
    planning: PlanningContext


def _normalize(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def detect_region(raw: RawFile, bundles: dict[str, Bundle]) -> Bundle:
    if not bundles:
        raise ValueError("Нет подготовленных регионов: запустите prepare и положите бандлы в data/bundles.")
    if raw.office_address:
        for bundle in bundles.values():
            if _normalize(bundle.office.address) == _normalize(raw.office_address):
                return bundle
    districts = [row.district for row in raw.rows]
    scores = {
        region: sum(1 for district in districts if district in {r.district for r in bundle.requests})
        for region, bundle in bundles.items()
    }
    region, score = max(scores.items(), key=lambda item: item[1])
    if not districts or score * 2 <= len(districts):
        raise ValueError(
            "Не удалось определить регион: адрес офиса и районы не совпадают ни с одним регионом."
        )
    return bundles[region]


def _set(record: DatasetRecord, **changes) -> None:
    with record.lock:
        for key, value in changes.items():
            setattr(record, key, value)


def _counting_geocoder(record: DatasetRecord, geocode: GeocodeFn) -> GeocodeFn:
    def wrapped(address: str, district: str) -> GeoResult:
        result = geocode(address, district)
        with record.lock:
            record.done += 1
        return result

    return wrapped


def _geocode_missing(record: DatasetRecord, requests: list[Request], geocode: GeocodeFn) -> list[Request]:
    missing = [r for r in requests if r.lat is None or r.lon is None]
    if not missing:
        return list(requests)
    _set(record, stage="geocoding", done=0, total=len(missing))
    counted = _counting_geocoder(record, geocode)
    result = []
    for request in requests:
        if request.lat is None or request.lon is None:
            geo = counted(request.address, request.district)
            request = request.model_copy(
                update={"lat": geo.lat, "lon": geo.lon, "geocode_precision": geo.precision}
            )
        result.append(request)
    return result


def _read_upload(
    record: DatasetRecord, filename: str, data: bytes, deps: IngestDeps
) -> tuple[PreparedDay, str, list[str]]:
    if filename.lower().endswith(".json"):
        try:
            bundle = Bundle.model_validate_json(data)
        except ValidationError as error:
            first = error.errors()[0]
            location = ".".join(str(part) for part in first["loc"])
            raise ValueError(f"JSON не соответствует схеме бандла: {location}: {first['msg']}") from error
        requests = _geocode_missing(record, bundle.requests, deps.geocode)
        day = PreparedDay(
            bundle.region, bundle.office.title, bundle.office, requests, bundle.engineers, bundle.control_plan
        )
        return day, "bundle", []

    raw = parse_beeline_csv(data)
    if raw.is_control:
        raise ValueError("Это файл «Контрольное распределение». Загрузите «Синтетические данные» региона.")
    if not raw.rows:
        raise ValueError("В файле нет ни одной заявки.")
    reference = detect_region(raw, deps.bundles.all())
    if [row.request_id for row in raw.rows] == [request.id for request in reference.requests]:
        requests, control = list(reference.requests), reference.control_plan
    else:
        _set(record, stage="geocoding", done=0, total=len(raw.rows))
        requests = build_requests(deps.synth_config, raw, None, _counting_geocoder(record, deps.geocode))
        control = None
    day = PreparedDay(
        reference.region, reference.office.title, reference.office, requests, reference.engineers, control
    )
    return day, "beeline_csv", raw.skipped


def preprocess_upload(record: DatasetRecord, filename: str, data: bytes, deps: IngestDeps) -> None:
    try:
        _set(record, stage="parsing")
        day, source, skipped = _read_upload(record, filename, data, deps)
        _set(record, stage="matrix")
        ctx = deps.planning
        problem = make_problem(
            day.requests, day.engineers, model=ctx.model, traffic=ctx.traffic, osrm=ctx.osrm, cache=ctx.cache
        )
        _set(record, stage="solving")
        session = start_session(
            record.dataset_id, day.region, day.office, day.requests, day.engineers, day.control, ctx
        )
        precision = Counter(request.geocode_precision for request in day.requests)
        report = UploadReport(
            region=day.region,
            region_title=day.region_title,
            source=source,
            requests=len(day.requests),
            engineers=len(day.engineers),
            skipped_rows=skipped,
            geocoding=GeocodingCounts(
                house=precision["house"],
                street=precision["street"],
                locality=precision["locality"],
                none=precision["none"],
            ),
            not_found=[
                NotFoundAddress(request_id=r.id, address=r.address)
                for r in day.requests
                if r.lat is None or r.lon is None
            ],
            matrix_source=problem.travel.base.source,
        )
        _set(record, prepared=day, session=session, report=report, status="ready", stage="ready")
    except ValueError as error:
        _set(record, status="failed", error=str(error))
    except Exception as error:  # noqa: BLE001 - любой сбой предподсчёта показываем пользователю
        _set(record, status="failed", error=f"Внутренняя ошибка предподсчёта: {error}")
