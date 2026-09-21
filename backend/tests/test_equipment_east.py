"""Утренний запас оборудования на настоящих данных Востока — самого тяжёлого по оборудованию региона.

Расстояния считаются формулой (OSRM в тестах не нужен), поэтому километры здесь не совпадают с таблицей
результатов в README. Проверяется не число километров, а то, что предел запаса действительно меняет план.
"""

from collections import Counter
from pathlib import Path

import pytest

from app.domain.models import DEFAULT_EQUIPMENT_STOCK
from app.geo.matrix import TrafficProfile, TravelModel
from app.ingest.bundle import load_bundle
from app.planning.workload import DEFAULT_WORKLOAD_LEVEL, travel_buffer, workload_weights
from app.settings import REPO_ROOT
from app.solvers.ortools_solver import OrToolsSolver
from app.solvers.problem import make_problem

BUNDLE = Path(REPO_ROOT) / "data" / "bundles" / "east" / "bundle.json"
TIME_LIMIT_S = 3


@pytest.fixture(scope="module")
def east():
    if not BUNDLE.exists():
        pytest.skip("нет собранного бандла Востока")
    return load_bundle(BUNDLE)


def _plan(bundle, stock):
    engineers = [e.model_copy(update={"equipment_stock": stock}) for e in bundle.engineers]
    problem = make_problem(
        bundle.requests,
        engineers,
        model=TravelModel(),
        traffic=TrafficProfile({}),
        buffer=travel_buffer(DEFAULT_WORKLOAD_LEVEL),
        lunch=True,
    )
    return OrToolsSolver(time_limit_s=TIME_LIMIT_S, weights=workload_weights(DEFAULT_WORKLOAD_LEVEL)).solve(
        problem
    )


def _per_brigade(bundle, plan):
    need = {request.id for request in bundle.requests if request.needs_equipment}
    return Counter(
        {
            route.engineer_id: sum(1 for visit in route.visits if visit.request_id in need)
            for route in plan.routes
        }
    )


def test_dispatchers_never_loaded_a_brigade_beyond_the_daily_stock(east):
    """Запас 6 единиц выбран по настоящему дню: диспетчеры не давали бригаде больше пяти."""
    assert sum(1 for request in east.requests if request.needs_equipment) == 40
    assert max(_per_brigade(east, east.control_plan).values()) <= DEFAULT_EQUIPMENT_STOCK


def test_a_stock_one_below_the_daily_one_changes_the_east_plan(east):
    """Запас на единицу меньше дневного (пять, максимум диспетчеров за этот день) уже меняет план Востока.

    Сколько единиц оптимум без предела кладёт в одну бригаду, зависит от поиска: здесь, по формуле и за 3 секунды,
    шесть, с поиском от 4 секунд — семь, и тогда план меняет уже дневной запас. Больше пяти выходит при любом
    проверенном лимите, от 1 до 30 секунд, поэтому тест держится за запас пять.
    """
    tight = DEFAULT_EQUIPMENT_STOCK - 1
    limited = _plan(east, tight)
    unlimited = _plan(east, 999)

    # Без предела оптимум складывает оборудование в одну бригаду плотнее, чем она увезла бы при запасе пять.
    assert max(_per_brigade(east, unlimited).values()) > tight
    assert max(_per_brigade(east, limited).values()) <= tight
    assert limited.metrics.violations == 0
    assert limited.metrics.unassigned == unlimited.metrics.unassigned == 0
    assert [(r.engineer_id, [v.request_id for v in r.visits]) for r in limited.routes] != [
        (r.engineer_id, [v.request_id for v in r.visits]) for r in unlimited.routes
    ]
