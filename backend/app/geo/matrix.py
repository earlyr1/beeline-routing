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


@dataclass(frozen=True)
class TravelModel:
    detour_factor: float = 1.3  # гаверсинус -> дорожное расстояние, если нет OSRM
    car_fallback_speed_kmh: float = 25.0
    foot_speed_kmh: float = 5.0
    foot_km_factor: float = 1.2  # пешеходный путь по автомобильному графу
    bike_speed_kmh: float = 20.0  # велосипед: маршрут как у авто, без пробок
    public_speed_kmh: float = 15.0
    public_wait_min: float = 10.0


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
                [fallback_km[i][j] if km_raw[i][j] is None else km_raw[i][j] for j in range(n)]
                for i in range(n)
            ]
            car_min = [
                [fallback_min[i][j] if min_raw[i][j] is None else min_raw[i][j] for j in range(n)]
                for i in range(n)
            ]
            return BaseMatrix(road_km, car_min, straight, "osrm")
    return BaseMatrix(fallback_km, fallback_min, straight, "haversine")


class TravelTimes:
    def __init__(self, base: BaseMatrix, model: TravelModel, traffic: TrafficProfile) -> None:
        self.base = base
        self.model = model
        self.traffic = traffic

    def km(self, i: int, j: int, transport: Transport) -> float:
        if i == j:
            return 0.0
        if transport in (Transport.CAR, Transport.BIKE):
            return self.base.road_km[i][j]
        if transport == Transport.FOOT:
            return self.base.road_km[i][j] * self.model.foot_km_factor
        return self.base.straight_km[i][j] * self.model.detour_factor

    def minutes(self, i: int, j: int, transport: Transport, slot_min: int) -> int:
        """Время в пути, целые минуты (вверх). slot_min: время, по которому берётся коэффициент пробок."""
        if i == j:
            return 0
        if transport == Transport.CAR:
            raw = self.base.car_min[i][j] * self.traffic.factor_at(slot_min)
        elif transport == Transport.BIKE:
            raw = self.km(i, j, transport) / self.model.bike_speed_kmh * 60.0
        elif transport == Transport.FOOT:
            raw = self.km(i, j, transport) / self.model.foot_speed_kmh * 60.0
        else:
            raw = self.km(i, j, transport) / self.model.public_speed_kmh * 60.0 + self.model.public_wait_min
        return math.ceil(raw - 1e-9)
