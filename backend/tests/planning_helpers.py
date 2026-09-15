from app.domain.models import Office
from app.geo.matrix import TrafficProfile, TravelModel
from app.planning.session import PlanningContext, start_session
from tests.helpers import OFFICE_LAT, OFFICE_LON, eng, req

OFFICE = Office(
    region="t", title="Тест", address="г. Москва, ул Юных Ленинцев, д 83с 4", lat=OFFICE_LAT, lon=OFFICE_LON
)


def context(**overrides):
    values = dict(model=TravelModel(), traffic=TrafficProfile({}), time_limit_s=1)
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


def new_session(ctx=None, requests=None, engineers=None):
    return start_session(
        "d_test",
        "t",
        OFFICE,
        requests or day_requests(),
        engineers or day_engineers(),
        None,
        ctx or context(),
    )


def routes(plan):
    return {route.engineer_id: [visit.request_id for visit in route.visits] for route in plan.routes}


def busy_engineer(plan):
    return next(route.engineer_id for route in plan.routes if route.visits)
