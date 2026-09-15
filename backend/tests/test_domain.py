import pytest
from pydantic import BaseModel, ValidationError

from app.domain.enums import EventType, Skill, Transport
from app.domain.models import Bundle, Engineer, Event, Office, Request
from app.domain.timeutil import HHMM, fmt_hhmm, parse_beeline_datetime, parse_hhmm


def test_parse_hhmm_accepts_strings_and_minutes():
    assert parse_hhmm("09:30") == 570
    assert parse_hhmm("0:01") == 1
    assert parse_hhmm(600) == 600


@pytest.mark.parametrize("bad", ["9-30", "25:61", "", True, -5])
def test_parse_hhmm_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_hhmm(bad)


def test_fmt_hhmm_pads_and_allows_after_midnight():
    assert fmt_hhmm(570) == "09:30"
    assert fmt_hhmm(1470) == "24:30"


def test_hhmm_allows_very_late_visits_in_dispatcher_plans():
    assert parse_hhmm(2933) == 2933
    assert parse_hhmm(fmt_hhmm(2933)) == 2933


def test_parse_beeline_datetime_drops_date():
    assert parse_beeline_datetime("17.08.2026 23:59") == 1439
    assert parse_beeline_datetime("17.08.2026 0:01") == 1


def test_hhmm_field_is_int_in_python_and_string_in_json():
    class Model(BaseModel):
        t: HHMM

    model = Model(t="10:05")
    assert model.t == 605
    assert model.model_dump() == {"t": 605}
    assert model.model_dump(mode="json") == {"t": "10:05"}


def _request(**overrides):
    data = dict(
        id="R1",
        address="Москва",
        duration_min=30,
        window_start="10:00",
        window_end="12:00",
        skill=Skill.LOCAL,
    )
    data.update(overrides)
    return Request(**data)


def test_request_rejects_inverted_window():
    with pytest.raises(ValidationError):
        _request(window_start="12:00", window_end="10:00")


def test_engineer_requires_one_to_three_skills():
    base = dict(
        id="E1",
        name="Иванов",
        start_lat=55.7,
        start_lon=37.6,
        shift_start="09:00",
        shift_end="18:00",
        transport=Transport.CAR,
    )
    with pytest.raises(ValidationError):
        Engineer(**base, skills=[])
    assert Engineer(**base, skills=[Skill.LOCAL]).skills == [Skill.LOCAL]


def test_event_payload_rules():
    with pytest.raises(ValidationError):
        Event(type=EventType.URGENT, time="13:00")
    with pytest.raises(ValidationError):
        Event(type=EventType.CANCEL, time="13:00")
    with pytest.raises(ValidationError):
        Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00")
    event = Event(type=EventType.URGENT, time="13:00", request=_request())
    assert event.request.id == "R1"


def test_transport_change_event_needs_engineer_and_transport():
    text = "для смены транспорта нужны engineer_id и transport"
    with pytest.raises(ValidationError, match=text):
        Event(type=EventType.ENGINEER_TRANSPORT_CHANGED, time="13:00", engineer_id="E1")
    with pytest.raises(ValidationError, match=text):
        Event(type="engineer_transport_changed", time="13:00", transport="bike")
    event = Event(type=EventType.ENGINEER_TRANSPORT_CHANGED, time="13:00", engineer_id="E1", transport="bike")
    assert (event.transport, event.previous_transport) == (Transport.BIKE, None)


def test_other_events_accept_missing_transport():
    event = Event(type=EventType.CANCEL, time="13:00", request_id="R1")
    assert (event.transport, event.previous_transport) == (None, None)
    dumped = event.model_dump(mode="json")
    assert (dumped["transport"], dumped["previous_transport"]) == (None, None)
    unavailable = Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id="E1")
    assert unavailable.transport is None


def test_bundle_json_roundtrip():
    bundle = Bundle(
        region="east",
        office=Office(region="east", title="Восток", address="Москва", lat=55.7, lon=37.6),
        requests=[_request()],
        engineers=[],
    )
    restored = Bundle.model_validate_json(bundle.model_dump_json())
    assert restored == bundle
    assert '"window_start":"10:00"' in bundle.model_dump_json()


def test_save_and_load_bundle(tmp_path):
    from app.ingest.bundle import load_bundle, save_bundle

    bundle = Bundle(
        region="east",
        office=Office(region="east", title="Восток", address="Москва", lat=55.7, lon=37.6),
        requests=[_request()],
        engineers=[],
    )
    path = tmp_path / "bundles" / "east" / "bundle.json"
    save_bundle(bundle, path)
    assert load_bundle(path) == bundle


def test_bundle_rejects_repeated_request_and_engineer_ids():
    office = Office(region="east", title="Восток", address="Москва", lat=55.7, lon=37.6)
    engineer = Engineer(
        id="E1",
        name="Иванов",
        start_lat=55.7,
        start_lon=37.6,
        shift_start="09:00",
        shift_end="18:00",
        skills=[Skill.LOCAL],
        transport=Transport.CAR,
    )
    requests = [_request(), _request(id="R2"), _request(), _request(id="R2"), _request(id="R3")]
    with pytest.raises(ValidationError, match="повторяются номера заявок: R1, R2"):
        Bundle(region="east", office=office, requests=requests, engineers=[engineer])
    with pytest.raises(ValidationError, match="повторяются номера инженеров: E1"):
        Bundle(region="east", office=office, requests=[_request()], engineers=[engineer, engineer])
