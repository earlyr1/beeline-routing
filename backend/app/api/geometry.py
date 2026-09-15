"""Линии маршрутов для карты: OSRM по дорогам или прямые отрезки."""

from __future__ import annotations

import json

import httpx

from app.api.schemas import RouteGeometry, RouteLeg
from app.domain.enums import Transport
from app.geo.kvcache import KVCache
from app.geo.osrm import LatLon, OsrmClient, OsrmError
from app.planning.session import PlanningSession


def _leg(
    start: LatLon, end: LatLon, osrm: OsrmClient | None, cache: KVCache | None
) -> tuple[list[list[float]], bool]:
    straight = [[start[1], start[0]], [end[1], end[0]]]
    if osrm is None:
        return straight, False
    key = "osrm-route:" + json.dumps([[round(v, 6) for v in start], [round(v, 6) for v in end]])
    cached = cache.get(key) if cache is not None else None
    if cached is not None:
        return json.loads(cached), True
    try:
        coordinates = osrm.route_geometry([start, end])
    except (httpx.HTTPError, OsrmError, KeyError, IndexError):
        return straight, False
    if cache is not None:
        cache.set(key, json.dumps(coordinates))
    return coordinates, True


def route_geometry(
    session: PlanningSession,
    engineer_id: str,
    which: str,
    osrm: OsrmClient | None,
    cache: KVCache | None,
) -> RouteGeometry:
    """Бросает LookupError с текстом для пользователя, если инженера или плана нет."""
    engineer = session.engineer(engineer_id)
    if engineer is None:
        raise LookupError(f"Инженер {engineer_id} не найден.")
    plan = session.plan if which == "current" else session.previous_plan
    if plan is None:
        raise LookupError("Предыдущего плана нет: событий ещё не было.")
    route = next((r for r in plan.routes if r.engineer_id == engineer_id), None)
    visits = route.visits if route is not None else []
    use_osrm = osrm if engineer.transport != Transport.PUBLIC else None
    position: LatLon = (engineer.start_lat, engineer.start_lon)
    legs: list[RouteLeg] = []
    all_roads = True
    for visit in visits:
        request = session.request(visit.request_id)
        if request is None or request.lat is None or request.lon is None:
            continue
        destination: LatLon = (request.lat, request.lon)
        coordinates, by_road = _leg(position, destination, use_osrm, cache)
        all_roads = all_roads and by_road
        legs.append(RouteLeg(to_request_id=visit.request_id, coordinates=coordinates))
        position = destination
    source = "osrm" if use_osrm is not None and all_roads else "straight"
    return RouteGeometry(engineer_id=engineer_id, transport=engineer.transport, source=source, legs=legs)
