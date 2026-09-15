import pytest
from pydantic import ValidationError

from app.domain.models import Event
from app.domain.validation_text import validation_message, validation_text
from app.llm.interpret import EngineerDelayArgs

DELAY = {"type": "engineer_delayed", "time": "13:00", "engineer_id": "E01", "delay_min": 30}


def _errors(model, payload):
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)
    return caught.value.errors()


@pytest.mark.parametrize(
    ("payload", "text"),
    [
        ({**DELAY, "delay_min": "abc"}, "delay_min: нужно целое число"),
        ({**DELAY, "delay_min": 12.5}, "delay_min: нужно целое число без дробной части"),
        ({key: value for key, value in DELAY.items() if key != "time"}, "time: обязательное поле"),
        ({**DELAY, "delay_min": 3}, "задержка должна быть от 5 до 480 минут"),
    ],
)
def test_event_errors_are_russian(payload, text):
    assert validation_text(_errors(Event, payload)) == text


def test_unknown_event_type_lists_allowed_values_in_russian():
    text = validation_text(_errors(Event, {**DELAY, "type": "boom"}))
    assert text.startswith("type: допустимые значения: ")
    assert "'engineer_delayed'" in text and " или " in text and " or " not in text


def test_llm_delay_arguments_are_russian():
    assert (
        validation_text(_errors(EngineerDelayArgs, {"engineer_id": "E01"}), limit=1)
        == "delay_min: обязательное поле"
    )
    assert validation_text(_errors(EngineerDelayArgs, {"engineer_id": "E01", "delay_min": "полчаса"})) == (
        "delay_min: нужно целое число"
    )


def test_unknown_error_type_falls_back_to_pydantic_message_and_skips_location_parts():
    item = {"type": "something_new", "loc": ("body", "field"), "msg": "Value error, текст"}
    assert validation_message(item) == "текст"
    assert (
        validation_text([item, item, item, item], skip=("body",))
        == "field: текст; field: текст; field: текст"
    )
