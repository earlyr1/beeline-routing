"""Сквозная проверка живого backend: загрузка, план, демо-события, объяснение, геометрия.

Внутри контейнера:  docker compose exec backend python scripts/smoke_api.py
С хоста (порт проброшен): python3 scripts/smoke_api.py http://127.0.0.1:8001/api ../data/raw/east_synthetic.csv
Только стандартная библиотека Python.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


class ApiError(Exception):
    def __init__(self, method: str, url: str, code: int, body: str) -> None:
        super().__init__(f"{method} {url} -> {code}: {body}")
        self.code = code
        self.detail = _detail(body)


def _detail(body: str) -> str:
    try:
        detail = json.loads(body).get("detail")
    except (ValueError, AttributeError):
        return body
    return detail if isinstance(detail, str) else body


def call(method: str, url: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise ApiError(method, url, error.code, error.read().decode("utf-8")) from error


def on_grid(event: dict, grid: list[dict]) -> dict:
    """Окно заявки события — на сетку окон сервера (GET /api/config).

    Демо-события лежат в бандле, то есть это ДАННЫЕ: сеткой они не проверяются и окно у них своё. Но уходят они
    в диспетчерский эндпоинт, а тот произвольный интервал не принимает, поэтому здесь окно кладётся на слот.
    Заявка «как можно скорее» не трогается: её окно задаёт сервер.
    """
    request = event.get("request")
    if not grid or not request or request.get("asap") or not request.get("window_start"):
        return event
    start = request["window_start"]
    slot = next((s for s in grid if s["start"] <= start < s["end"]), None)
    if slot is None:
        # Окно вне рабочего дня своего слота не имеет: берём ближайший, как это делает сервер.
        slot = grid[0] if start < grid[0]["start"] else grid[-1]
    return {**event, "request": {**request, "window_start": slot["start"], "window_end": slot["end"]}}


def send_events(base: str, dataset_id: str, events: list[dict], state: dict) -> dict:
    """Отправляет события по очереди. Отклонённое событие (422) печатается, проверка идёт дальше."""
    for event in events:
        started = time.monotonic()
        try:
            state = call(
                "POST",
                f"{base}/datasets/{dataset_id}/events",
                json.dumps(event).encode("utf-8"),
                {"Content-Type": "application/json"},
            )
        except ApiError as error:
            if error.code != 422:
                raise
            print(f"событие {event['type']} в {event['time']} отклонено ({error.code}): {error.detail}")
            continue
        diff = state["last_diff"]
        print(
            f"событие {event['type']} в {event['time']} за {time.monotonic() - started:.1f} с: "
            f"перенесено {len(diff['moved'])}, добавлено {len(diff['added'])}, снято {len(diff['removed'])}, "
            f"сдвигов времени {len(diff['time_shifts'])}"
        )
    return state


def upload(base: str, path: Path) -> str:
    boundary = uuid.uuid4().hex
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    status = call(
        "POST", f"{base}/upload", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    return status["dataset_id"]


def metrics_line(label: str, plan: dict | None) -> None:
    if plan is None:
        return
    m = plan["metrics"]
    print(
        f"{label}: инженеров {m['engineers_used']}, км {m['total_km']}, назначено {m['assigned']}, "
        f"не назначено {m['unassigned']}, нарушений {m['violations']}"
    )


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else "http://127.0.0.1:8001/api"
    path = Path(argv[2] if len(argv) > 2 else "/app/data/bundles/east/bundle.json")
    config = call("GET", f"{base}/config")
    print("config:", config)

    started = time.monotonic()
    dataset_id = upload(base, path)
    while (status := call("GET", f"{base}/datasets/{dataset_id}"))["status"] == "processing":
        time.sleep(0.5)
    if status["status"] != "ready":
        raise SystemExit(f"Предподсчёт завершился ошибкой: {status['error']}")
    report = status["report"]
    print(
        f"готово за {time.monotonic() - started:.1f} с: регион {report['region']}, источник {report['source']}, "
        f"заявок {report['requests']}, матрица {report['matrix_source']}"
    )

    state = call("POST", f"{base}/datasets/{dataset_id}/plan")
    metrics_line("OR-Tools", state["plan"])
    metrics_line("FCFS", state["baseline"])
    metrics_line("Диспетчеры", state["control"])

    raw = json.loads(path.read_text(encoding="utf-8")).get("events", []) if path.suffix == ".json" else []
    grid = config.get("window_grid", [])
    events = [on_grid(event, grid) for event in raw]
    state = send_events(base, dataset_id, events, state)
    if events:
        metrics_line("OR-Tools после событий", state["plan"])

    visit = next(v for route in state["plan"]["routes"] for v in route["visits"] if not v["pinned"])
    explanation = call("GET", f"{base}/datasets/{dataset_id}/explain/{visit['request_id']}")
    print("объяснение:", explanation["summary"])

    for route in [r for r in state["plan"]["routes"] if r["visits"]][:4]:
        geometry = call("GET", f"{base}/datasets/{dataset_id}/routes/{route['engineer_id']}/geometry")
        points = sum(len(leg["coordinates"]) for leg in geometry["legs"])
        print(
            f"геометрия {route['engineer_id']} ({geometry['transport']}): {geometry['source']}, точек {points}"
        )
    print("OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except ApiError as error:
        raise SystemExit(str(error)) from error
