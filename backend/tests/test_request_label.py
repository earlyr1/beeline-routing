"""Подпись срочной заявки «URG-<номер>» в сообщениях сервера: диспетчер видит один номер и в тексте, и в карточке."""

import pytest

from app.domain.enums import EventType, Priority, request_label
from app.domain.models import Event, Request
from app.planning.session import EventRejected, apply_event, check_event
from tests.helpers import req
from tests.planning_helpers import context, day_requests, new_session


def urgent_day(ctx, **changes):
    """День со срочной заявкой Билайна 57866: номер заявки остаётся сырым, подпись для диспетчера — «URG-57866»."""
    urgent = req("57866", 1, 1, "13:00", "15:00", priority=Priority.URGENT).model_copy(update=changes)
    return new_session(ctx=ctx, requests=[*day_requests(), urgent])


def test_request_label_marks_only_urgent_requests():
    assert request_label("57866", Priority.URGENT) == "URG-57866"
    assert request_label("57866", Priority.NORMAL) == "57866"
    # Заявку диспетчера фронт нумерует уже с приставкой: второй раз она не добавляется.
    assert request_label("URG-M3K7P", Priority.URGENT) == "URG-M3K7P"


def test_event_rejections_sign_an_urgent_request_of_the_day():
    ctx = context()
    session = urgent_day(ctx)
    owner = next(
        (
            route.engineer_id
            for route in session.plan.routes
            for visit in route.visits
            if visit.request_id == "57866"
        ),
        None,
    )
    assert owner is not None
    same = Request.model_validate(session.request("57866").model_dump())
    cases = [
        (
            Event(type=EventType.RESTORE, time="12:00", request_id="57866"),
            "Заявка URG-57866 не отменена, возвращать нечего.",
        ),
        (
            Event(type=EventType.REQUEST_UPDATED, time="12:00", request_id="57866", request=same),
            "В заявке URG-57866 ничего не изменилось.",
        ),
        (
            Event(type=EventType.REQUEST_REASSIGNED, time="12:00", request_id="57866", engineer_id=owner),
            f"Заявка URG-57866 уже у {session.engineer(owner).name}.",
        ),
        (
            Event(type=EventType.URGENT, time="12:00", request=req("57866", 2, 2, "16:00", "17:00")),
            "Заявка с номером URG-57866 уже есть в плане.",
        ),
    ]
    for event, text in cases:
        with pytest.raises(EventRejected) as rejected:
            check_event(session, event, ctx)
        assert str(rejected.value) == text


def test_reassignment_of_an_urgent_request_without_a_point_is_signed():
    """Адрес срочной заявки не нашёлся: её нет в задаче, и отказ переназначения подписан по заявкам дня."""
    ctx = context()
    session = urgent_day(ctx, lat=None, lon=None, geocode_precision="none")
    event = Event(type=EventType.REQUEST_REASSIGNED, time="12:00", request_id="57866", engineer_id="E1")
    with pytest.raises(EventRejected) as rejected:
        apply_event(session, event, ctx)
    assert str(rejected.value) == "У заявки URG-57866 нет точки на карте, назначить её нельзя."
