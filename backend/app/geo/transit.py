"""Время на общественном транспорте от 2ГИС: клиент Distance Matrix API и файлы матриц.

Встроенная модель считает общественный транспорт и пешком по расстоянию по прямой (TravelModel): быстрее из двух,
пешком или поездкой, без расписаний и без зависимости от часа. 2ГИС считает по настоящим маршрутам, поэтому
матрицы посчитаны демо-ключом один раз на регион (scripts/transit_matrix.py) и лежат рядом с бандлами.

У каждого региона своя матрица и свой файл <регион>.json в каталоге матриц (Settings.transit_dir). Планировщик
берёт минуты по парам точек (TransitLookup): точка дня привязывается к ближайшей точке матрицы не дальше 150 м,
поэтому ни порядок точек, ни метры расхождения геокодера не важны. После срочной заявки или смены адреса пары прежних
точек остаются из 2ГИС, а пары с новой точкой, которой рядом в матрице нет, считает встроенная модель.

Посчитанные матрицы лежат в репозитории (data/transit/) и копируются в образ backend, поэтому минуты 2ГИС
у сервиса есть и без ключа, и без сети. Ключ нужен только чтобы пересчитать матрицы, берётся он только из
переменной окружения TWOGIS_API_KEY: он не пишется в файл, не логируется и не попадает в текст ошибки.
"""

from __future__ import annotations

import bisect
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx

from app.geo.haversine import haversine_km
from app.geo.osrm import LatLon

TRANSIT_URL = "https://routing.api.2gis.com/get_dist_matrix"
API_VERSION = "2.0"
# Демо-ключ 2ГИС принимает матрицу не больше 10×10 (источники × назначения): на больший запрос он отвечает
# «permissible dimension of the matrix is exceeded», поэтому матрица считается блоками 10×10.
MAX_BLOCK = 10
# Лимиты демо-ключа: 10 запросов и 1000 элементов в минуту останавливают выдачу, 1000 запросов в месяц
# блокируют ключ. Запрос 10×10 — до 100 элементов, так что оба минутных лимита означают 10 запросов в минуту.
# Пауза 7.5 с даёт не больше 9 запросов в любой минуте, в том числе календарной: 900 элементов с запасом.
REQUESTS_PER_MINUTE = 10
ELEMENTS_PER_MINUTE = 1000
REQUESTS_PER_MONTH = 1000
DEFAULT_PAUSE_S = 7.5
# Демо-ключ не строит маршруты между точками дальше 50 км друг от друга: на такой запрос он отвечает 403 «excessive
# distance between points for demo-keys, max (km): 50». Поэтому точки делятся на группы, где любая пара ближе 45 км
# по прямой (запас на то, что 2ГИС меряет расстояние по-своему), и 2ГИС считает только пары внутри группы. Пар между
# группами (Москва и Кашира в Юго-востоке, от 60 км) в матрице нет, и в них сервис берёт встроенную модель.
DEMO_MAX_DISTANCE_KM = 50.0
GROUP_DISTANCE_KM = 45.0
# Если ключ всё же упёрся в минутный лимит (ответ 429), ждём минуту и повторяем тот же запрос: иначе сбой
# на середине большого региона сжёг бы все его уже потраченные запросы.
RATE_LIMIT_STATUS = 429
RATE_LIMIT_WAIT_S = 61.0
RATE_LIMIT_RETRIES = 3
KEY_ENV = "TWOGIS_API_KEY"

# Форма запроса и ответа Distance Matrix API 2ГИС собрана в одном месте: если API изменится, правится только этот
# словарь, остальной код имён полей не знает. Форма проверена живым запросом 16.09.2026: режим задаёт поле transport,
# а виды транспорта — обязательный список public_transport_params.transport. С полем type 2ГИС отвечает 422
# «type is invalid», без списка видов — 400 «public_transport_params is not found».
TRANSIT_REQUEST = {
    "points": "points",  # тело запроса: список точек
    "lat": "lat",  # точка: широта
    "lon": "lon",  # точка: долгота
    "sources": "sources",  # тело запроса: индексы точек-источников в points
    "targets": "targets",  # тело запроса: индексы точек-назначений в points
    "mode": "transport",  # тело запроса: режим расчёта
    "public_transport": "public_transport",  # значение режима: общественный транспорт
    "params": "public_transport_params",  # тело запроса: настройки общественного транспорта
    "kinds": "transport",  # настройки: какими видами транспорта можно ехать
    "departure": "start_time",  # тело запроса: время выезда, RFC 3339 со смещением часового пояса
    "routes": "routes",  # ответ: список элементов матрицы
    "source_id": "source_id",  # элемент ответа: индекс точки-источника в points
    "target_id": "target_id",  # элемент ответа: индекс точки-назначения в points
    "duration": "duration",  # элемент ответа: время в пути, секунды
    "status": "status",  # элемент ответа: удалось ли построить маршрут
    "ok": "OK",  # значение status для найденного маршрута
}


# Виды общественного транспорта Москвы, которыми может ехать инженер: всё городское, включая МЦК и МЦД.
PUBLIC_TRANSPORT_KINDS = (
    "bus",
    "trolleybus",
    "tram",
    "shuttle_bus",
    "metro",
    "light_metro",
    "monorail",
    "suburban_train",
    "mcc",
    "mcd",
)


class TransitError(RuntimeError):
    """Сбой 2ГИС: в сообщении статус HTTP и начало ответа. Ключ в сообщение не попадает."""


def point_key(lat: float, lon: float) -> tuple[float, float]:
    """Точка в том виде, в каком она лежит в файле: округление до 6 знаков, как в build_base_matrix."""
    return round(lat, 6), round(lon, 6)


def points_key(points: Sequence[LatLon]) -> list[list[float]]:
    """Точки в том виде, в каком они лежат в файле: округление до 6 знаков, как в build_base_matrix."""
    return [list(point_key(lat, lon)) for lat, lon in points]


def split_blocks(count: int, size: int = MAX_BLOCK) -> list[list[int]]:
    """Индексы точек блоками не больше size: столько 2ГИС берёт с одной стороны запроса."""
    return [list(range(start, min(start + size, count))) for start in range(0, count, size)]


def distance_groups(points: Sequence[LatLon], limit_km: float = GROUP_DISTANCE_KM) -> list[list[int]]:
    """Индексы точек группами, в которых каждая пара не дальше limit_km по прямой: такие запросы демо-ключ примет.

    Точка идёт в первую группу, где она близка ко всем, иначе открывает новую. Порядок точек внутри группы сохраняется.
    """
    groups: list[list[int]] = []
    for index, (lat, lon) in enumerate(points):
        for group in groups:
            if all(haversine_km(lat, lon, *points[other]) <= limit_km for other in group):
                group.append(index)
                break
        else:
            groups.append([index])
    return groups


def request_pairs(points: Sequence[LatLon], size: int = MAX_BLOCK) -> list[tuple[list[int], list[int]]]:
    """Запросы расчёта: пары блоков не больше size точек, оба блока из одной группы близких точек."""
    pairs: list[tuple[list[int], list[int]]] = []
    for group in distance_groups(points):
        blocks = [[group[place] for place in block] for block in split_blocks(len(group), size)]
        pairs.extend((sources, targets) for sources in blocks for targets in blocks)
    return pairs


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
        TRANSIT_REQUEST["params"]: {TRANSIT_REQUEST["kinds"]: list(PUBLIC_TRANSPORT_KINDS)},
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
    """Матрица времени на общественном транспорте от 2ГИС блоками 10×10 точек."""

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
        """Минуты между всеми парами точек. None в ячейке: 2ГИС не нашёл маршрут или точки в разных группах.

        progress вызывается после каждого блока: номер блока с 1 и всего блоков.
        """
        points = list(points)
        size = len(points)
        minutes: list[list[int | None]] = [[None] * size for _ in range(size)]
        pairs = request_pairs(points)
        for done, (sources, targets) in enumerate(pairs, start=1):
            if done > 1:
                self._sleep(self.pause_s)
            self._fill(minutes, points, sources, targets, departure)
            if progress is not None:
                progress(done, len(pairs))
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
        for _ in range(RATE_LIMIT_RETRIES):
            if response.status_code != RATE_LIMIT_STATUS:
                break
            self._sleep(RATE_LIMIT_WAIT_S)
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
    """Матрица минут на общественном транспорте: файл региона из data/transit, он же едет в образе backend."""

    departure: str  # время выезда HH:MM, на которое считали
    points: list[list[float]]  # [[lat, lon], ...] с округлением до 6 знаков, порядок как в задаче
    minutes: list[list[int | None]]
    source: str = "2gis"
    region: str = ""  # регион, для которого считали: он же имя файла в каталоге матриц

    def matches(self, points: Sequence[LatLon]) -> bool:
        """Файл посчитан ровно на этих точках в этом порядке: так скрипт решает, что регион пересчитывать не нужно."""
        return self.points == points_key(points)

    def minutes_at(self, i: int, j: int) -> float | None:
        """Минуты в ячейке или None: маршрута нет либо ячейки нет в файле."""
        if not 0 <= i < len(self.minutes) or not 0 <= j < len(self.minutes[i]):
            return None
        value = self.minutes[i][j]
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value)


# Точка дня берёт минуты ближайшей точки матрицы не дальше этого радиуса. Координаты одного адреса у геокодеров
# расходятся на метры, клик по карте в точку файла не попадает, а дробные координаты не обязаны совпасть до знака.
# По самим матрицам 2ГИС минуты у точек до 200 м друг от друга совпадают по медиане, p90 расхождения 3–4 минуты;
# дальше ошибка растёт (200–400 м: медиана 2, p90 6). Пешую добавку за смещение не делаем: 2ГИС сам доводит пешком
# до той же остановки, и добавка только ухудшает оценку (медиана расхождения с ней 0.7–2.5 минуты вместо 0).
SNAP_RADIUS_KM = 0.15
# Градус широты не короче 110.5 км: полоса широт поиска с запасом накрывает радиус.
_KM_PER_LAT_DEGREE = 110.5


@dataclass(frozen=True)
class Snap:
    """Привязка точки дня к точке матрицы: все места этой точки в файле и смещение до неё."""

    key: tuple[float, float]  # координаты точки матрицы с округлением до 6 знаков
    places: tuple[int, ...]  # места точки в файле: одни координаты бывают в матрице несколько раз
    offset_km: float


class _MatrixPoints:
    """Точки одной матрицы, отсортированные по широте: ближайшая ищется только в полосе широт радиуса."""

    def __init__(self, matrix: TransitMatrix) -> None:
        self._order = sorted(range(len(matrix.points)), key=lambda index: matrix.points[index][0])
        self._lats = [matrix.points[index][0] for index in self._order]
        self._points = matrix.points
        self._places: dict[tuple[float, float], list[int]] = {}
        for index, (lat, lon) in enumerate(matrix.points):
            self._places.setdefault(point_key(lat, lon), []).append(index)

    def snap(self, lat: float, lon: float, radius_km: float) -> Snap | None:
        """Ближайшая точка матрицы не дальше radius_km; из равных по расстоянию — первая в файле."""
        band = radius_km / _KM_PER_LAT_DEGREE
        low = bisect.bisect_left(self._lats, lat - band)
        high = bisect.bisect_right(self._lats, lat + band)
        best: tuple[float, int] | None = None
        for index in self._order[low:high]:
            distance = haversine_km(lat, lon, *self._points[index])
            if distance <= radius_km and (best is None or (distance, index) < best):
                best = (distance, index)
        if best is None:
            return None
        key = point_key(*self._points[best[1]])
        return Snap(key=key, places=tuple(self._places[key]), offset_km=best[0])


class TransitLookup:
    """Минуты 2ГИС между точками задачи, пара за парой: из матрицы, к точкам которой привязались обе точки пары.

    Точка дня привязывается в каждой матрице к ближайшей точке не дальше radius_km, а не ищется по месту в списке или
    точному совпадению координат. Поэтому матрице не нужно совпадать с задачей целиком, а метры расхождения геокодера
    не выбивают точку из 2ГИС. Минут нет, и время считает встроенная модель, если: рядом с одной из точек пары в
    матрице ничего нет (новая заявка, новый адрес), точки привязались к матрицам разных регионов, обе привязались к
    одной и той же точке матрицы (2ГИС дал бы 0 минут между разными местами) или 2ГИС не нашёл маршрут.
    """

    def __init__(
        self,
        points: Sequence[LatLon],
        matrices: Sequence[TransitMatrix],
        radius_km: float = SNAP_RADIUS_KM,
    ) -> None:
        self._matrices = list(matrices)
        indexes = [_MatrixPoints(matrix) for matrix in self._matrices]
        # Привязка каждой точки дня в каждой матрице, None — рядом в этой матрице ничего нет.
        self._snaps = [[index.snap(lat, lon, radius_km) for index in indexes] for lat, lon in points]
        # Минуты считаются сразу на все пары: солверы спрашивают время много раз, а точек в дне сотня-другая.
        size = len(self._snaps)
        self._minutes = [[self._pair(i, j) for j in range(size)] for i in range(size)]
        # Сколько упорядоченных пар разных узлов задачи берут минуты из 2ГИС.
        self.covered = sum(
            1
            for i, row in enumerate(self._minutes)
            for j, value in enumerate(row)
            if i != j and value is not None
        )
        offsets = [min(snap.offset_km for snap in row if snap) for row in self._snaps if any(row)]
        # Сколько точек дня привязалось хотя бы к одной матрице и самое большое смещение среди них.
        self.snapped = len(offsets)
        self.max_offset_km = max(offsets, default=0.0)

    def _pair(self, i: int, j: int) -> float | None:
        """Число из матрицы с наименьшим смещением пары; внутри матрицы — первое число по местам точек."""
        best: tuple[float, float] | None = None
        for matrix, source, target in zip(self._matrices, self._snaps[i], self._snaps[j], strict=True):
            if source is None or target is None or source.key == target.key:
                continue
            value = next(
                (
                    minutes
                    for place in source.places
                    for other in target.places
                    if (minutes := matrix.minutes_at(place, other)) is not None
                ),
                None,
            )
            offset = source.offset_km + target.offset_km
            if value is not None and (best is None or offset < best[0]):
                best = (offset, value)
        return best[1] if best is not None else None

    def minutes(self, i: int, j: int) -> float | None:
        """Минуты 2ГИС между узлами задачи i и j или None: пара не из одной матрицы или маршрута нет."""
        return self._minutes[i][j]


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

    Отсутствие файла — не ошибка: сервис считает общественный транспорт встроенной моделью.
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
    регионы встроенной моделью. Ошибкой это не считается.
    """
    try:
        paths = sorted(Path(directory).glob("*.json"))
    except OSError:
        return []
    matrices = (load_transit_matrix(path) for path in paths)
    return [matrix for matrix in matrices if matrix is not None]
