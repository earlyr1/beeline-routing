"""Мини-задачи для тестов солверов: координаты задаются в километрах от офиса."""

import math

from app.domain.enums import Priority, Skill, Transport
from app.domain.models import Engineer, Request
from app.geo.matrix import TrafficProfile, TravelModel
from app.solvers.problem import make_problem

OFFICE_LAT, OFFICE_LON = 55.75, 37.60
ALL_SKILLS = (Skill.LOCAL, Skill.CONNECTION, Skill.EMERGENCY)


def at(x_km: float, y_km: float) -> tuple[float, float]:
    lat = OFFICE_LAT + y_km / 111.0
    lon = OFFICE_LON + x_km / (111.0 * math.cos(math.radians(OFFICE_LAT)))
    return lat, lon


def req(
    request_id,
    x_km,
    y_km,
    window_start,
    window_end,
    *,
    skill=Skill.LOCAL,
    duration=30,
    priority=Priority.NORMAL,
    transport=None,
):
    lat, lon = at(x_km, y_km)
    return Request(
        id=request_id,
        address=f"адрес {request_id}",
        lat=lat,
        lon=lon,
        geocode_precision="house",
        duration_min=duration,
        window_start=window_start,
        window_end=window_end,
        priority=priority,
        skill=skill,
        transport_required=transport,
    )


def eng(
    engineer_id,
    *,
    skills=ALL_SKILLS,
    transport=Transport.CAR,
    shift=("09:00", "18:00"),
    available=True,
    unavailable_from=None,
    start=(0.0, 0.0),  # стартовая точка в километрах от офиса
):
    start_lat, start_lon = at(*start)
    return Engineer(
        id=engineer_id,
        name=f"Инженер {engineer_id}",
        start_lat=start_lat,
        start_lon=start_lon,
        shift_start=shift[0],
        shift_end=shift[1],
        skills=list(skills),
        transport=transport,
        available=available,
        unavailable_from=unavailable_from,
    )


def problem_of(requests, engineers, *, model=None):
    return make_problem(requests, engineers, model=model or TravelModel(), traffic=TrafficProfile({}))
