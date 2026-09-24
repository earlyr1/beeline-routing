from typing import Any

from app.domain.models import Office
from app.geo.matrix import TrafficProfile, TravelModel
from app.planning.session import PlanningContext, start_session
from app.planning.workload import DEFAULT_WORKLOAD_LEVEL, WORKLOAD_LEVELS
from app.solvers.problem import NO_BUFFER
from tests.helpers import OFFICE_LAT, OFFICE_LON, eng, req

OFFICE = Office(
    region="t", title="Тест", address="г. Москва, ул Юных Ленинцев, д 83с 4", lat=OFFICE_LAT, lon=OFFICE_LON
)


def context(**overrides):
    values: dict[str, Any] = dict(
        model=TravelModel(), traffic=TrafficProfile({}), time_limit_s=1, time_limit_lunch_s=1
    )
    values.update(overrides)
    return PlanningContext(**values)


def day_requests():
    return [
        req("R1", 1, 0, "10:00", "12:00"),
        req("R2", 1.2, 0, "14:00", "16:00"),
        req("R3", -1, 0, "15:00", "17:00"),
    ]


def day_engineers():
    return [eng("E1"), eng("E2")]


def new_session(
    ctx=None, requests=None, engineers=None, workload_level=DEFAULT_WORKLOAD_LEVEL, lunch_enabled=True
):
    return start_session(
        "d_test",
        "t",
        OFFICE,
        requests or day_requests(),
        engineers or day_engineers(),
        None,
        ctx or context(),
        workload_level=workload_level,
        lunch_enabled=lunch_enabled,
    )


def routes(plan):
    return {route.engineer_id: [visit.request_id for visit in route.visits] for route in plan.routes}


def busy_engineer(plan):
    return next(route.engineer_id for route in plan.routes if route.visits)


# Уровень нагрузки без запаса на дорогу («На пределе»). На нём планируются сценарии, где минуты выездов,
# задержек и переездов посчитаны вручную прямо по матрице; уровень нагрузки в этих сценариях не проверяется.
EXACT_TRAVEL_LEVEL = next(
    level for level, item in enumerate(WORKLOAD_LEVELS) if item.travel_buffer == NO_BUFFER
)

# На уровне EXACT_TRAVEL_LEVEL окна задают порядок A, B, C без ожиданий: A 09:16–09:46, к B выезд в 09:46, прибытие и начало
# в 09:59, C с 10:42. В 09:47 инженер уже в пути к B.
IN_TRANSIT_TO_B = "09:47"


def transit_requests():
    return [
        req("A", 5, 0, "09:00", "09:30"),
        req("B", 5, 4, "09:30", "10:15"),
        req("C", 1, 4, "10:30", "17:00"),
    ]


def transit_session(ctx=None):
    return new_session(ctx=ctx, requests=transit_requests(), workload_level=EXACT_TRAVEL_LEVEL)


def other_engineer(engineer_id):
    return "E2" if engineer_id == "E1" else "E1"


def visit_times(plan, engineer_id):
    route = next(route for route in plan.routes if route.engineer_id == engineer_id)
    return [(visit.request_id, visit.arrival, visit.start, visit.end) for visit in route.visits]
