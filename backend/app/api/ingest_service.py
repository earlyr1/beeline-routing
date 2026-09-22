"""Фоновый предподсчёт дня: разбор, регион, геокодинг, матрица, первый план.

Тем же путём идут загруженный файл и подготовленный регион с экрана загрузки: у них различается только то, откуда
берутся заявки и бригады, а дальше день собирается одинаково и одинаково подхватывает ночной план региона.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydantic import ValidationError

from app.api.registry import DatasetRecord, PreparedDay
from app.api.schemas import GeocodingCounts, NotFoundAddress, ScenarioInfo, UploadReport
from app.domain.models import Bundle, Request
from app.ingest.beeline_csv import RawFile, RawRequestRow, parse_beeline_csv
from app.ingest.bundle import load_bundle
from app.ingest.geocode import GeoResult
from app.ingest.window_check import check_windows
from app.planning.session import PlanningContext, start_session
from app.solvers.problem import make_problem
from app.synth.config import SynthConfig
from app.synth.requests import build_requests

GeocodeFn = Callable[[str, str], GeoResult]
logger = logging.getLogger(__name__)

# Сколько причин пропуска уходит в текст ошибки, когда в файле не осталось ни одной заявки.
SKIPPED_IN_ERROR = 3


class BundleStore:
    """Подготовленные бандлы регионов из data/bundles/<region>/bundle.json (читаются один раз).

    Нечитаемый бандл одного региона не уносит с собой остальные: такой регион пропускается, в списке кнопок его
    нет, а на запрос его дня сервис отвечает по-русски. Иначе один повреждённый файл ломал бы весь экран загрузки.
    """

    def __init__(self, bundles_dir: Path) -> None:
        self._dir = Path(bundles_dir)
        self._bundles: dict[str, Bundle] | None = None
        self._broken: set[str] = set()
        self._lock = threading.Lock()

    def all(self) -> dict[str, Bundle]:
        with self._lock:
            if self._bundles is None:
                bundles: dict[str, Bundle] = {}
                for path in sorted(self._dir.glob("*/bundle.json")):
                    region = path.parent.name
                    try:
                        bundles[region] = load_bundle(path)
                    except (ValueError, OSError) as error:
                        self._broken.add(region)
                        logger.warning("Бандл региона %r не прочитан, регион пропущен: %s", region, error)
                self._bundles = bundles
            return self._bundles

    def is_broken(self, region: str) -> bool:
        """Бандл региона лежит в каталоге, но не читается: повреждён или не соответствует схеме."""
        self.all()
        with self._lock:
            return region in self._broken


@dataclass(frozen=True)
class FileNotes:
    """Что отчёт разбора говорит о самом файле: пропущенные строки и замечания к окнам заявок."""

    skipped: list[str] = field(default_factory=list)
    windows: list[str] = field(default_factory=list)


@dataclass
class IngestDeps:
    bundles: BundleStore
    synth_config: SynthConfig
    geocode: GeocodeFn
    planning: PlanningContext


def scenarios(deps: IngestDeps) -> list[ScenarioInfo]:
    """Подготовленные регионы: у них есть и бандл в data/bundles, и описание в synth_config.yaml.

    Порядок — как в конфиге: настоящие регионы Билайна идут раньше сгенерированного нами. Название берётся оттуда
    же, откуда его взял prepare для офиса бандла, поэтому кнопка и заголовок отчёта после загрузки совпадают.
    """
    bundles = deps.bundles.all()
    return [
        ScenarioInfo(
            region=region,
            title=config.title,
            requests=len(bundle.requests),
            engineers=len(bundle.engineers),
            generated=config.generated,
        )
        for region, config in deps.synth_config.regions.items()
        if (bundle := bundles.get(region)) is not None
    ]


def scenario_bundle(deps: IngestDeps, region: str) -> Bundle:
    """Бандл подготовленного региона; LookupError — региона нет в конфиге, его бандл не собран или не читается."""
    config = deps.synth_config.regions.get(region)
    if config is None:
        raise LookupError(f"Регион {region} не найден: такого подготовленного региона нет.")
    bundle = deps.bundles.all().get(region)
    if bundle is None:
        if deps.bundles.is_broken(region):
            raise LookupError(f"Регион «{config.title}» не читается: бандл data/bundles/{region} повреждён.")
        raise LookupError(f"Регион «{config.title}» не подготовлен: нет бандла в data/bundles/{region}.")
    return bundle


def is_generated(deps: IngestDeps, region: str) -> bool:
    """Регион сгенерирован нами: выгрузки Билайна по нему нет (docs/assumptions.md)."""
    config = deps.synth_config.regions.get(region)
    return config is not None and config.generated


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


# Поля датасета, которые переживают перезапуск: статус предподсчёта, его стадия, отчёт разбора и ошибка.
# Счётчик адресов (done, total) в хранилище не идёт: он тикает на каждый адрес, а день, пойманный перезапуском
# на предподсчёте, всё равно не возобновляется.
_PERSISTED = frozenset({"status", "stage", "report", "error"})


def _set(record: DatasetRecord, **changes) -> None:
    with record.lock:
        for key, value in changes.items():
            setattr(record, key, value)
        if _PERSISTED & changes.keys():
            record.save_status()


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


def _drop_repeated_ids(raw: RawFile) -> RawFile:
    """Оставляет первую строку с каждым номером заявки, повторы попадают в отчёт о пропущенных строках."""
    seen: set[str] = set()
    rows: list[RawRequestRow] = []
    skipped = list(raw.skipped)
    for row in raw.rows:
        if row.request_id in seen:
            skipped.append(
                f"строка {row.line_no}: номер заявки {row.request_id} повторяется, строка пропущена"
            )
            continue
        seen.add(row.request_id)
        rows.append(replace(row, row_index=len(rows)))
    return replace(raw, rows=rows, skipped=skipped)


def _bundle_day(record: DatasetRecord, bundle: Bundle, deps: IngestDeps) -> PreparedDay:
    """День из бандла: заявкам без координат ищутся адреса, остальное берётся как есть."""
    requests = _geocode_missing(record, bundle.requests, deps.geocode)
    return PreparedDay(
        bundle.region,
        bundle.office.title,
        bundle.office,
        requests,
        bundle.engineers,
        bundle.control_plan,
        is_generated(deps, bundle.region),
    )


def _nothing_left(raw: RawFile) -> str:
    """Почему в файле не осталось заявок: пустой он или его целиком съели пропущенные строки.

    Причины пропуска дальше нигде не покажутся — отчёта разбора у неудавшейся загрузки нет, — поэтому первые
    из них идут прямо в текст ошибки: диспетчеру важно понять, что дело в окнах, а не в формате файла.
    """
    if not raw.skipped:
        return "В файле нет ни одной заявки."
    shown = "; ".join(raw.skipped[:SKIPPED_IN_ERROR])
    tail = " …" if len(raw.skipped) > SKIPPED_IN_ERROR else ""
    return f"В файле нет ни одной заявки: пропущены все строки ({len(raw.skipped)}). {shown}{tail}"


def _is_reference_file(raw: RawFile, reference: Bundle) -> bool:
    """Файл — та самая выгрузка, из которой собран бандл региона: те же заявки и те же окна.

    Только тогда можно взять готовый бандл с контрольным распределением диспетчеров вместо разбора файла.
    Совпадения одних номеров мало: подправленное в Excel окно номера заявки не меняет, и день построился бы
    по окнам бандла, а не по окнам файла — ровно та тихая подмена данных, которой мы не делаем. У восьми
    файлов `data/raw` окна выгрузки и окна бандла совпадают, так что нетронутый файл остаётся на этом пути.
    """
    return [(row.request_id, row.window_start, row.window_end) for row in raw.rows] == [
        (request.id, request.window_start, request.window_end) for request in reference.requests
    ]


def _read_upload(
    record: DatasetRecord, filename: str, data: bytes, deps: IngestDeps
) -> tuple[PreparedDay, str, FileNotes]:
    if filename.lower().endswith(".json"):
        try:
            bundle = Bundle.model_validate_json(data)
        except ValidationError as error:
            first = error.errors()[0]
            location = ".".join(str(part) for part in first["loc"])
            message = str(first["msg"]).removeprefix("Value error, ")
            detail = f"{location}: {message}" if location else message
            raise ValueError(f"JSON не соответствует схеме бандла: {detail}") from error
        return _bundle_day(record, bundle, deps), "bundle", FileNotes()

    raw = parse_beeline_csv(data)
    if raw.is_control:
        raise ValueError("Это файл «Контрольное распределение». Загрузите «Синтетические данные» региона.")
    # Повторы уносятся до проверки окон: замечание про окно строки, которой в дне всё равно не будет, диспетчера
    # только запутает, да и знаменатель «столько-то из скольких» должен считать заявки дня.
    raw = _drop_repeated_ids(raw)
    # Окна выгрузки проверяются до всего остального, но не правятся: строка, в окне которой работать нельзя,
    # выпадает, остальные замечания уходят в отчёт разбора (app/ingest/window_check.py).
    raw = check_windows(deps.synth_config, raw)
    if not raw.rows:
        raise ValueError(_nothing_left(raw))
    reference = detect_region(raw, deps.bundles.all())
    if _is_reference_file(raw, reference):
        requests, control = list(reference.requests), reference.control_plan
    else:
        _set(record, stage="geocoding", done=0, total=len(raw.rows))
        requests = build_requests(deps.synth_config, raw, _counting_geocoder(record, deps.geocode))
        control = None
    day = PreparedDay(
        reference.region,
        reference.office.title,
        reference.office,
        requests,
        reference.engineers,
        control,
        is_generated(deps, reference.region),
    )
    return day, "beeline_csv", FileNotes(raw.skipped, raw.window_warnings)


ReadDay = Callable[[], tuple[PreparedDay, str, FileNotes]]


def _preprocess(record: DatasetRecord, read: ReadDay, deps: IngestDeps) -> None:
    """Общий путь дня: read даёт заявки, бригады и замечания к файлу, дальше матрица, план и отчёт."""
    try:
        _set(record, stage="parsing")
        day, source, notes = read()
        _set(record, stage="matrix")
        ctx = deps.planning
        problem = make_problem(
            day.requests,
            day.engineers,
            model=ctx.model,
            traffic=ctx.traffic,
            osrm=ctx.osrm,
            cache=ctx.cache,
            transit=ctx.transit,
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
            generated=day.generated,
            requests=len(day.requests),
            engineers=len(day.engineers),
            skipped_rows=notes.skipped,
            window_warnings=notes.windows,
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
        with record.lock:
            # Входные данные дня уходят в хранилище вместе с утренним планом: одна правка — одна транзакция.
            record.start_day(session, prepared=day)
            _set(record, report=report, status="ready", stage="ready")
    except ValueError as error:
        _set(record, status="failed", error=str(error))
    except Exception as error:  # noqa: BLE001 - любой сбой предподсчёта показываем пользователю
        _set(record, status="failed", error=f"Внутренняя ошибка предподсчёта: {error}")


def preprocess_upload(record: DatasetRecord, filename: str, data: bytes, deps: IngestDeps) -> None:
    """Предподсчёт загруженного файла: выгрузка Билайна CSV или готовый бандл JSON."""
    _preprocess(record, lambda: _read_upload(record, filename, data, deps), deps)


def preprocess_scenario(record: DatasetRecord, bundle: Bundle, deps: IngestDeps) -> None:
    """Предподсчёт подготовленного региона: тот же путь, что у загруженного бандла, только файл не разбирается."""
    _preprocess(record, lambda: (_bundle_day(record, bundle, deps), "scenario", FileNotes()), deps)
