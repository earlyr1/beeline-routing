import importlib.util
import json
from pathlib import Path

import pytest

SMOKE = Path(__file__).resolve().parents[1] / "scripts" / "smoke_api.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("smoke_api", SMOKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state(added):
    return {
        "last_diff": {"moved": [], "added": added, "removed": [], "time_shifts": []},
        "plan": {"metrics": {}},
    }


EVENTS = [
    {"type": "cancel", "time": "13:00", "request_id": "41077"},
    {"type": "urgent", "time": "13:00", "request": {"id": "URG-001"}},
]


def test_rejected_demo_event_is_printed_and_next_events_are_sent(monkeypatch, capsys):
    smoke = _load_smoke()
    sent = []

    def fake_call(method, url, body=None, headers=None):
        event = json.loads(body)
        sent.append(event["type"])
        if event["type"] == "cancel":
            detail = json.dumps({"detail": "Заявка 41077 уже в работе с 12:00, отменить её нельзя."})
            raise smoke.ApiError(method, url, 422, detail)
        return _state([{"request_id": "URG-001"}])

    monkeypatch.setattr(smoke, "call", fake_call)
    initial = _state([])
    state = smoke.send_events("http://backend/api", "d_1", EVENTS, initial)

    assert sent == ["cancel", "urgent"]
    output = capsys.readouterr().out
    assert "событие cancel в 13:00 отклонено (422): Заявка 41077 уже в работе с 12:00" in output
    assert "добавлено 1" in output
    assert state["last_diff"]["added"] == [{"request_id": "URG-001"}]


def test_other_http_errors_still_stop_the_check(monkeypatch):
    smoke = _load_smoke()

    def fake_call(method, url, body=None, headers=None):
        raise smoke.ApiError(method, url, 500, "Internal Server Error")

    monkeypatch.setattr(smoke, "call", fake_call)
    with pytest.raises(smoke.ApiError):
        smoke.send_events("http://backend/api", "d_1", EVENTS, _state([]))
