"""Время на общественном транспорте от 2ГИС: клиент Distance Matrix API и локальные файлы матриц.

Наша встроенная модель считает общественный транспорт грубо: расстояние по прямой ×1.3 при 15 км/ч плюс 10 минут
ожидания, без расписаний, без метро и без зависимости от часа. 2ГИС считает по настоящим маршрутам, поэтому
диспетчер может один раз посчитать матрицы демо-ключом (scripts/transit_matrix.py) и подложить их сервису.

У каждого региона своя матрица и свой файл <регион>.json в каталоге матриц (Settings.transit_dir): точки в файле
идут в порядке задачи дня, поэтому матрица подходит только своему региону, и планировщик выбирает её по точкам.

Условия 2ГИС запрещают хранить результаты: файлы остаются на машине диспетчера, в репозиторий они не попадают
(data/transit/ в .gitignore) и после демо их можно удалить. Ключ берётся только из переменной окружения
TWOGIS_API_KEY: он не пишется в файл, не логируется и не попадает в текст ошибки.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx

from app.geo.osrm import LatLon

TRANSIT_URL = "https://routing.api.2gis.com/get_dist_matrix"
API_VERSION = "2.0"
# 2ГИС берёт не больше 25 точек с каждой стороны запроса, поэтому матрица считается блоками 25×25.
MAX_BLOCK = 25
# Лимиты демо-ключа 2ГИС: 10 запросов и 1000 элементов в минуту останавливают выдачу, 1000 запросов
# в месяц блокируют ключ. Блок 25×25 — это 625 элементов, поэтому шлём не больше одного запроса
# в минуту: так минутные лимиты не задеваются, даже если они считаются по календарной минуте.
REQUESTS_PER_MINUTE = 10
ELEMENTS_PER_MINUTE = 1000
REQUESTS_PER_MONTH = 1000
DEFAULT_PAUSE_S = 61.0
KEY_ENV = "TWOGIS_API_KEY"

# Форма запроса и ответа Distance Matrix API 2ГИС собрана в одном месте. Имена полей тот, кто запускает расчёт,
# сверяет с официальной документацией https://docs.2gis.com/ru/api/navigation/distance-matrix/overview в момент
# запуска: если API изменился, правится только этот словарь, остальной код имён полей не знает.
TRANSIT_REQUEST = {
    "points": "points",  # тело запроса: список точек
    "lat": "lat",  # точка: широта
    "lon": "lon",  # точка: долгота
    "sources": "sources",  # тело запроса: индексы точек-источников в points
    "targets": "targets",  # тело запроса: индексы точек-назначений в points
    "mode": "type",  # тело запроса: режим расчёта
    "public_transport": "public_transport",  # значение режима: общественный транспорт
    "departure": "start_time",  # тело запроса: время выезда, RFC 3339 со смещением часового пояса
    "routes": "routes",  # ответ: список элементов матрицы
    "source_id": "source_id",  # элемент ответа: индекс точки-источника в points
    "target_id": "target_id",  # элемент ответа: индекс точки-назначения в points
    "duration": "duration",  # элемент ответа: время в пути, секунды
    "status": "status",  # элемент ответа: удалось ли построить маршрут
    "ok": "OK",  # значение status для найденного маршрута
}


class TransitError(RuntimeError):
    """Сбой 2ГИС: в сообщении статус HTTP и начало ответа. Ключ в сообщение не попадает."""


def points_key(points: Sequence[LatLon]) -> list[list[float]]:
    """Точки в том виде, в каком они лежат в файле: округление до 6 знаков, как в build_base_matrix."""
    return [[round(lat, 6), round(lon, 6)] for lat, lon in points]


def split_blocks(count: int, size: int = MAX_BLOCK) -> list[list[int]]:
    """Индексы точек блоками не больше size: столько 2ГИС берёт с одной стороны запроса."""
    return [list(range(start, min(start + size, count))) for start in range(0, count, size)]


def request_body(
    points: Sequence[LatLon], sources: Sequence[int], targets: Sequence[int], departure: str
) -> dict:
    """Тело запроса к 2ГИС. sources и targets — индексы внутри points этого запроса."""
    return {
        TRANSIT_REQUEST["points"]: [
            {TRANSIT_REQUEST["lat"]: lat, TRANSIT_REQUEST["lon"]: lon} for lat, lon in points_key(points)
        ],
        TRANSIT_REQUEST["sources"]: list(sources),
        TRANSIT_REQUEST["targets"]: list(targets),
        TRANSIT_REQUEST["mode"]: TRANSIT_REQUEST["public_transport"],
        TRANSIT_REQUEST["departure"]: departure_timestamp(departure),
    }


# Часовой пояс Москвы: 2ГИС требует время выезда в RFC 3339 со смещением, иначе отвечает 400.
MOSCOW_OFFSET = "+03:00"
# День недели выгрузки: 17.08.2026 — понедельник. Прошлую дату брать нельзя, у расписаний её нет.
DEPARTURE_WEEKDAY = 0


def departure_timestamp(departure: str, today: date | None = None) -> str:
    """Время выезда «HH:MM» в RFC 3339 на ближайший будущий понедельник: «2026-09-21T13:00:00+03:00».

    Данные сервиса — будний понедельник, а 2ГИС считает по расписаниям, которых на прошедшие даты нет. Сегодняшний
    понедельник не берётся: расписание на уже идущий день может быть неполным.
    """
    today = today or date.today()
    ahead = (DEPARTURE_WEEKDAY - today.weekday()) % 7 or 7
    return f"{today + timedelta(days=ahead):%Y-%m-%d}T{departure}:00{MOSCOW_OFFSET}"


def _failure(response: httpx.Response) -> str:
    """Текст ошибки: статус и начало ответа. Адрес запроса не берём — в нём ключ."""
    return f"2ГИС ответил {response.status_code}: {response.text[:200]}"


def _element_minutes(route: dict) -> int | None:
    """Минуты элемента матрицы или None, если 2ГИС не нашёл маршрут между точками."""
    if route.get(TRANSIT_REQUEST["status"], TRANSIT_REQUEST["ok"]) != TRANSIT_REQUEST["ok"]:
        return None
    seconds = route.get(TRANSIT_REQUEST["duration"])
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return None
    return math.ceil(seconds / 60.0 - 1e-9)


class TransitClient:
    """Матрица времени на общественном транспорте от 2ГИС блоками 25×25 точек."""

    def __init__(
        self,
        key: str,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
        pause_s: float = DEFAULT_PAUSE_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = key
        self._client = client or httpx.Client(timeout=timeout)
        self.pause_s = pause_s
        self._sleep = sleep

    def matrix(
        self,
        points: Sequence[LatLon],
        departure: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[list[int | None]]:
        """Минуты между всеми парами точек. None в ячейке: 2ГИС не нашёл маршрут.

        progress вызывается после каждого блока: номер блока с 1 и всего блоков.
        """
        points = list(points)
        size = len(points)
        minutes: list[list[int | None]] = [[None] * size for _ in range(size)]
        blocks = split_blocks(size)
        total = len(blocks) * len(blocks)
        done = 0
        for sources in blocks:
            for targets in blocks:
                if done:
                    self._sleep(self.pause_s)
                self._fill(minutes, points, sources, targets, departure)
                done += 1
                if progress is not None:
                    progress(done, total)
        return minutes

    def _fill(
        self,
        minutes: list[list[int | None]],
        points: Sequence[LatLon],
        sources: Sequence[int],
        targets: Sequence[int],
        departure: str,
    ) -> None:
        """Один запрос по паре блоков. Точки блоков идут в одном списке, индексы в ответе — места в нём."""
        order = list(sources) if list(sources) == list(targets) else [*sources, *targets]
        local = {point: place for place, point in enumerate(order)}
        body = request_body(
            [points[point] for point in order],
            [local[point] for point in sources],
            [local[point] for point in targets],
            departure,
        )
        for route in self._post(body):
            if not isinstance(route, dict):
                raise TransitError(f"2ГИС ответил 200: элемент матрицы не объект ({str(route)[:200]})")
            try:
                source = order[route[TRANSIT_REQUEST["source_id"]]]
                target = order[route[TRANSIT_REQUEST["target_id"]]]
            except (KeyError, IndexError, TypeError) as error:
                raise TransitError(
                    f"2ГИС ответил 200: в элементе матрицы нет точек ({str(route)[:200]})"
                ) from error
            minutes[source][target] = _element_minutes(route)

    def _post(self, body: dict) -> list:
        response = self._client.post(
            TRANSIT_URL, params={"key": self._key, "version": API_VERSION}, json=body
        )
        if response.status_code != 200:
            raise TransitError(_failure(response))
        try:
            routes = response.json()[TRANSIT_REQUEST["routes"]]
        except (ValueError, KeyError, TypeError) as error:
            raise TransitError(_failure(response)) from error
        if not isinstance(routes, list):
            raise TransitError(_failure(response))
        return routes


@dataclass(frozen=True)
class TransitMatrix:
    """Матрица минут на общественном транспорте: локальный файл диспетчера, в репозиторий не попадает."""

    departure: str  # время выезда HH:MM, на которое считали
    points: list[list[float]]  # [[lat, lon], ...] с округлением до 6 знаков, порядок как в задаче
    minutes: list[list[int | None]]
    source: str = "2gis"
    region: str = ""  # регион, для которого считали: он же имя файла в каталоге матриц

    def matches(self, points: Sequence[LatLon]) -> bool:
        """Файл подходит задаче, только если точки те же и идут в том же порядке."""
        return self.points == points_key(points)

    def minutes_at(self, i: int, j: int) -> float | None:
        """Минуты в ячейке или None: маршрута нет либо ячейки нет в файле."""
        if not 0 <= i < len(self.minutes) or not 0 <= j < len(self.minutes[i]):
            return None
        value = self.minutes[i][j]
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value)


def build_transit_matrix(
    points: Sequence[LatLon],
    minutes: Sequence[Sequence[int | None]],
    departure: str,
    region: str = "",
) -> TransitMatrix:
    return TransitMatrix(
        departure=departure,
        points=points_key(points),
        minutes=[list(row) for row in minutes],
        region=region,
    )


def transit_matrix_path(directory: Path, region: str) -> Path:
    """Файл матрицы региона в каталоге матриц: у каждого региона свой, иначе они затирают друг друга."""
    return Path(directory) / f"{region}.json"


def save_transit_matrix(matrix: TransitMatrix, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": matrix.source,
        "region": matrix.region,
        "departure": matrix.departure,
        "points": matrix.points,
        "minutes": matrix.minutes,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_transit_matrix(path: Path) -> TransitMatrix | None:
    """Матрица из файла или None: файла нет, он не читается или формат не тот.

    Отсутствие файла — не ошибка: сервис считает общественный транспорт встроенной моделью, как до 2ГИС.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    points, minutes = data.get("points"), data.get("minutes")
    if not isinstance(points, list) or not isinstance(minutes, list):
        return None
    try:
        rows = [[float(lat), float(lon)] for lat, lon in points]
        cells: list[list[int | None]] = [list(row) for row in minutes]
    except (TypeError, ValueError):
        return None
    if len(cells) != len(rows) or any(len(row) != len(rows) for row in cells):
        return None
    return TransitMatrix(
        departure=str(data.get("departure") or ""),
        points=rows,
        minutes=cells,
        source=str(data.get("source") or "2gis"),
        region=str(data.get("region") or ""),
    )


def load_transit_matrices(directory: Path) -> list[TransitMatrix]:
    """Все матрицы каталога, по файлу на регион, в порядке имён файлов.

    Каталога нет, файл не читается или формат не тот — такой файл просто пропускается: сервис посчитает эти
    регионы встроенной моделью, как до 2ГИС. Ошибкой это не считается.
    """
    try:
        paths = sorted(Path(directory).glob("*.json"))
    except OSError:
        return []
    matrices = (load_transit_matrix(path) for path in paths)
    return [matrix for matrix in matrices if matrix is not None]
