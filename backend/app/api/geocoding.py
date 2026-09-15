"""Адрес по точке на карте: диспетчер ставит ручную заявку кликом, адрес подставляется сам."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.routes import Deps
from app.api.schemas import PointAddress
from app.ingest.geocode import in_region

router = APIRouter(prefix="/api/geocode")

OUTSIDE_REGION = "Точка вне Москвы и Московской области."


@router.get("/reverse", response_model=PointAddress)
def reverse(lat: float, lon: float, deps: Deps) -> PointAddress:
    """Короткий адрес точки; без геокодера или при его ошибке — address null, точка остаётся без адреса."""
    if not in_region(lat, lon):
        raise HTTPException(status_code=422, detail=OUTSIDE_REGION)
    found = deps.reverse_geocode(lat, lon)
    return PointAddress(address=found.address, precision=found.precision)
