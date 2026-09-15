"""Клиент OSRM: матрица расстояний/времени и геометрия маршрута (профиль driving)."""

from __future__ import annotations

from collections.abc import Sequence

import httpx

LatLon = tuple[float, float]


class OsrmError(RuntimeError):
    pass


class OsrmClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    @staticmethod
    def _coords(points: Sequence[LatLon]) -> str:
        return ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)

    def _get(self, path: str, params: dict[str, str]) -> dict:
        response = self._client.get(f"{self._base_url}{path}", params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "Ok":
            raise OsrmError(data.get("message") or data.get("code") or "OSRM error")
        return data

    def table(self, points: Sequence[LatLon]) -> tuple[list[list[float | None]], list[list[float | None]]]:
        """Возвращает (километры, минуты). None в ячейке: точка недостижима."""
        data = self._get(f"/table/v1/driving/{self._coords(points)}", {"annotations": "duration,distance"})
        km = [[None if value is None else value / 1000.0 for value in row] for row in data["distances"]]
        minutes = [[None if value is None else value / 60.0 for value in row] for row in data["durations"]]
        return km, minutes

    def route_geometry(self, points: Sequence[LatLon]) -> list[list[float]]:
        """Линия маршрута через точки по порядку, координаты [lon, lat] (GeoJSON)."""
        data = self._get(
            f"/route/v1/driving/{self._coords(points)}", {"overview": "full", "geometries": "geojson"}
        )
        return data["routes"][0]["geometry"]["coordinates"]

    def health(self) -> bool:
        try:
            self._get("/nearest/v1/driving/37.617600,55.755800", {})
        except (httpx.HTTPError, OsrmError):
            return False
        return True
