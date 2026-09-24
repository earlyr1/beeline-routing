"""Матрицы времени и расстояния с учётом типа транспорта и часа суток."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from app.domain.enums import Transport
from app.geo.haversine import haversine_km
from app.geo.kvcache import KVCache
from app.geo.osrm import LatLon, OsrmClient, OsrmError
from app.geo.transit import TransitLookup


@dataclass(frozen=True)
class TravelModel:
    detour_factor: float = 1.3  # гаверсинус -> дорожное расстояние, если нет OSRM
    car_fallback_speed_kmh: float = 25.0
    bike_speed_kmh: float = 20.0  # велосипед: маршрут как у авто, без пробок
    # Общественный транспорт и пешком там, где нет минут 2ГИС: быстрее из двух, пешком или поездкой, по расстоянию
    # d по прямой. Пешком d ×detour_factor при 5 км/ч, то есть 15.6·d минут; поездка 22.5 + 2.8·d минут, в них
    # дойти до остановки, подождать и пересесть. Пешком быстрее до 1.76 км. Формула подобрана по ~23 тыс. пар
    # настоящих матриц 2ГИС (scripts/transit_error.py): средняя ошибка 19% против 28% у прежней «×1.3 при 15 км/ч
    # плюс 10 минут ожидания», в пределах ±25% 73% пар против 62%. Северо-запад медленнее формулы примерно на
    # 10 минут (реки и железные дороги).
    walk_speed_kmh: float = 5.0
    public_ride_base_min: float = 22.5
    public_ride_min_per_km: float = 2.8
    # Предел одного плеча по типу транспорта: бригада без машины не поедет через полобласти, даже если по смене
    # проходит. 15 км на велосипеде — 45 минут при 20 км/ч; 25 км на общественном транспорте — около 1 часа
    # 15 минут: предел сравнивается с километрами дороги, а 25 км дороги — это 19 км по прямой, то есть
    # 22.5 + 2.8·19 ≈ 76 минут по формуле _public_minutes. У автомобиля предела нет: в Каширу и Ступино
    # диспетчеры и правда отправляют бригады на машинах. Предел в километрах, а не в минутах: так он не плывёт
    # от коэффициента пробок и запаса на дорогу и совпадает с числом, которое диспетчер видит в интерфейсе.
    bike_leg_limit_km: float = 15.0
    public_leg_limit_km: float = 25.0

    def leg_limit_km(self, transport: Transport) -> float | None:
        """Предел одного плеча для транспорта или None, если предела нет."""
        if transport == Transport.BIKE:
            return self.bike_leg_limit_km
        if transport == Transport.PUBLIC:
            return self.public_leg_limit_km
        return None


@dataclass(frozen=True)
class TrafficProfile:
    """Коэффициент к времени OSRM (свободные дороги) по часу суток."""

    factors: dict[int, float] = field(default_factory=dict)

    def factor_at(self, minute_of_day: int) -> float:
        hour = max(0, min(23, minute_of_day // 60))
        return self.factors.get(hour, 1.0)

    @classmethod
    def load(cls, path: Path) -> TrafficProfile:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls({int(hour): float(value) for hour, value in (data.get("factors") or {}).items()})


@dataclass(frozen=True)
class BaseMatrix:
    road_km: list[list[float]]
    car_min: list[list[float]]
    straight_km: list[list[float]]
    source: str  # "osrm" | "haversine"


def _points_key(points: Sequence[LatLon]) -> str:
    payload = json.dumps([[round(lat, 6), round(lon, 6)] for lat, lon in points])
    return "osrm-table:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_base_matrix(
    points: Sequence[LatLon],
    model: TravelModel,
    osrm: OsrmClient | None = None,
    cache: KVCache | None = None,
) -> BaseMatrix:
    n = len(points)
    straight = [[haversine_km(*points[i], *points[j]) for j in range(n)] for i in range(n)]
    fallback_km = [[d * model.detour_factor for d in row] for row in straight]
    fallback_min = [[d / model.car_fallback_speed_kmh * 60.0 for d in row] for row in fallback_km]
    if osrm is not None and n > 0:
        key = _points_key(points)
        cached = cache.get(key) if cache is not None else None
        table = json.loads(cached) if cached is not None else None
        if table is None:
            try:
                table = osrm.table(points)
            except (httpx.HTTPError, OsrmError):
                table = None
            if table is not None and cache is not None:
                cache.set(key, json.dumps(table))
        if table is not None:
            km_raw, min_raw = table
            road_km = [
                [fallback_km[i][j] if (km := km_raw[i][j]) is None else km for j in range(n)]
                for i in range(n)
            ]
            car_min = [
                [fallback_min[i][j] if (mins := min_raw[i][j]) is None else mins for j in range(n)]
                for i in range(n)
            ]
            return BaseMatrix(road_km, car_min, straight, "osrm")
    return BaseMatrix(fallback_km, fallback_min, straight, "haversine")


class TravelTimes:
    def __init__(
        self,
        base: BaseMatrix,
        model: TravelModel,
        traffic: TrafficProfile,
        transit: TransitLookup | None = None,
    ) -> None:
        self.base = base
        self.model = model
        self.traffic = traffic
        # Минуты 2ГИС по парам узлов для общественного транспорта, если диспетчер посчитал матрицы.
        # Километры от них не зависят.
        self.transit = transit

    def km(self, i: int, j: int, transport: Transport) -> float:
        if i == j:
            return 0.0
        if transport in (Transport.CAR, Transport.BIKE):
            return self.base.road_km[i][j]
        return self.base.straight_km[i][j] * self.model.detour_factor

    def minutes(self, i: int, j: int, transport: Transport, slot_min: int) -> int:
        """Время в пути, целые минуты (вверх). slot_min: время, по которому берётся коэффициент пробок."""
        if i == j:
            return 0
        if transport == Transport.CAR:
            raw = self.base.car_min[i][j] * self.traffic.factor_at(slot_min)
        elif transport == Transport.BIKE:
            raw = self.km(i, j, transport) / self.model.bike_speed_kmh * 60.0
        else:
            raw = self._public_minutes(i, j)
        return math.ceil(raw - 1e-9)

    def _public_minutes(self, i: int, j: int) -> float:
        """Минуты 2ГИС, если обе точки пары в одной матрице и в ячейке число; иначе быстрее из «пешком» и поездки."""
        from_2gis = self.transit.minutes(i, j) if self.transit is not None else None
        if from_2gis is not None:
            return from_2gis
        walk = self.km(i, j, Transport.PUBLIC) / self.model.walk_speed_kmh * 60.0
        ride = (
            self.model.public_ride_base_min + self.model.public_ride_min_per_km * self.base.straight_km[i][j]
        )
        return min(walk, ride)
