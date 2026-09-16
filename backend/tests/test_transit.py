"""Время на общественном транспорте от 2ГИС: клиент, файл матрицы и подстановка в планировщик. Всё без сети."""

import json

import httpx
import pytest

from app.api.deps import build_deps
from app.domain.enums import Transport
from app.geo.matrix import BaseMatrix, TrafficProfile, TravelModel, TravelTimes
from app.geo.transit import (
    DEFAULT_PAUSE_S,
    ELEMENTS_PER_MINUTE,
    MAX_BLOCK,
    TRANSIT_REQUEST,
    TransitClient,
    TransitError,
    build_transit_matrix,
    load_transit_matrix,
    save_transit_matrix,
)
from app.settings import Settings
from app.solvers.problem import make_problem
from tests.helpers import at, eng, req
from tests.planning_helpers import context, day_engineers, day_requests, new_session

KEY = "демо-ключ-2гис"
POINTS = [at(0, 0), at(3, 0), at(0, 4)]
MANY = [at(k, 0) for k in range(60)]  # 3 блока: 25 + 25 + 10


def _rows(body: dict, seconds: int = 600) -> list[dict]:
    """Ответ 2ГИС на этот запрос: все пары блока, время в секундах."""
    return [
        {
            TRANSIT_REQUEST["source_id"]: source,
            TRANSIT_REQUEST["target_id"]: target,
            TRANSIT_REQUEST["duration"]: 0 if source == target else seconds,
            TRANSIT_REQUEST["status"]: TRANSIT_REQUEST["ok"],
        }
        for source in body[TRANSIT_REQUEST["sources"]]
        for target in body[TRANSIT_REQUEST["targets"]]
    ]


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json={TRANSIT_REQUEST["routes"]: _rows(body)})


def _client(handler, **kwargs) -> TransitClient:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TransitClient(KEY, client=client, sleep=lambda _: None, **kwargs)


def _points(requests, engineers):
    """Точки задачи в порядке make_problem."""
    return [(e.start_lat, e.start_lon) for e in engineers] + [(r.lat, r.lon) for r in requests]


def test_request_body_and_url_follow_the_documented_shape():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.url, body))
        return _ok(body)

    minutes = _client(handler).matrix(POINTS, "13:00")

    url, body = seen[0]
    assert url.host == "routing.api.2gis.com" and url.path == "/get_dist_matrix"
    assert url.params["key"] == KEY and url.params["version"] == "2.0"
    assert body[TRANSIT_REQUEST["points"]] == [
        {TRANSIT_REQUEST["lat"]: round(lat, 6), TRANSIT_REQUEST["lon"]: round(lon, 6)} for lat, lon in POINTS
    ]
    assert body[TRANSIT_REQUEST["sources"]] == [0, 1, 2]
    assert body[TRANSIT_REQUEST["targets"]] == [0, 1, 2]
    assert body[TRANSIT_REQUEST["mode"]] == TRANSIT_REQUEST["public_transport"]
    assert body[TRANSIT_REQUEST["departure"]].endswith("T13:00:00")
    assert minutes[0][1] == 10 and minutes[0][0] == 0


def test_points_are_split_into_blocks_of_25_with_a_call_per_block_pair():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append((len(body[TRANSIT_REQUEST["sources"]]), len(body[TRANSIT_REQUEST["targets"]])))
        assert len(body[TRANSIT_REQUEST["points"]]) <= 2 * MAX_BLOCK
        return _ok(body)

    minutes = _client(handler).matrix(MANY, "13:00")

    assert MAX_BLOCK == 25
    assert len(calls) == 9
    assert sorted(calls) == sorted([(a, b) for a in (25, 25, 10) for b in (25, 25, 10)])
    assert sum(sources * targets for sources, targets in calls) == 60 * 60
    assert len(minutes) == 60 and all(len(row) == 60 for row in minutes)
    assert minutes[59][0] == 10 and minutes[0][59] == 10 and minutes[30][31] == 10


def test_client_sleeps_between_calls_and_reports_progress():
    pauses, steps = [], []

    def handler(request):
        return _ok(json.loads(request.content))

    client = TransitClient(
        KEY,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        pause_s=0.5,
        sleep=pauses.append,
    )
    client.matrix(MANY[:30], "13:00", progress=lambda done, total: steps.append((done, total)))

    # Блок 25×25 — 625 элементов, минутный лимит 1000: один запрос в минуту в него укладывается.
    assert DEFAULT_PAUSE_S > 60.0 and MAX_BLOCK * MAX_BLOCK <= ELEMENTS_PER_MINUTE
    assert TransitClient(KEY).pause_s == DEFAULT_PAUSE_S
    # 30 точек: 2 блока, 4 запроса и 3 паузы между ними, перед первым запросом паузы нет.
    assert pauses == [0.5, 0.5, 0.5]
    assert steps == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_http_error_becomes_transit_error_without_the_key():
    def handler(request):
        return httpx.Response(429, text="Превышен лимит запросов. " + "x" * 500)

    with pytest.raises(TransitError) as error:
        _client(handler).matrix(POINTS, "13:00")

    message = str(error.value)
    assert "429" in message and "Превышен лимит запросов." in message
    assert KEY not in message and len(message) <= 260


def test_malformed_body_becomes_transit_error_without_the_key():
    def handler(request):
        return httpx.Response(200, text="<html>не JSON</html>")

    with pytest.raises(TransitError) as error:
        _client(handler).matrix(POINTS, "13:00")
    assert "200" in str(error.value) and KEY not in str(error.value)

    def without_routes(request):
        return httpx.Response(200, json={"generation_time": 12})

    with pytest.raises(TransitError) as error:
        _client(without_routes).matrix(POINTS, "13:00")
    assert KEY not in str(error.value)


def test_unreachable_element_stays_none():
    def handler(request):
        body = json.loads(request.content)
        rows = _rows(body)
        rows[1][TRANSIT_REQUEST["status"]] = "FAIL"  # пара (0, 1)
        rows[1].pop(TRANSIT_REQUEST["duration"])
        return httpx.Response(200, json={TRANSIT_REQUEST["routes"]: rows})

    minutes = _client(handler).matrix(POINTS, "13:00")
    assert minutes[0][1] is None and minutes[0][2] == 10


def test_transit_matrix_round_trip_keeps_six_decimals(tmp_path):
    matrix = build_transit_matrix(POINTS, [[0, 12, None], [12, 0, 30], [30, 25, 0]], "13:00")
    path = tmp_path / "transit_matrix.json"

    save_transit_matrix(matrix, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source"] == "2gis" and payload["departure"] == "13:00"
    assert payload["points"] == [[round(lat, 6), round(lon, 6)] for lat, lon in POINTS]
    assert payload["minutes"][0][2] is None
    loaded = load_transit_matrix(path)
    assert loaded == matrix and loaded.matches(POINTS)


def test_missing_broken_and_mismatched_files_are_not_an_error(tmp_path):
    assert load_transit_matrix(tmp_path / "нет-файла.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{не json", encoding="utf-8")
    assert load_transit_matrix(broken) is None
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"source": "2gis", "departure": "13:00"}), encoding="utf-8")
    assert load_transit_matrix(wrong) is None

    matrix = build_transit_matrix(POINTS, [[0, 12, 20], [12, 0, 30], [30, 25, 0]], "13:00")
    assert not matrix.matches(list(reversed(POINTS)))
    assert not matrix.matches(POINTS[:2])
    assert not matrix.matches([*POINTS[:2], at(9, 9)])


def _base(straight_km: float = 8.0) -> BaseMatrix:
    n = 3
    road = [[0.0 if i == j else straight_km * 1.3 for j in range(n)] for i in range(n)]
    car = [[0.0 if i == j else 20.0 for j in range(n)] for i in range(n)]
    straight = [[0.0 if i == j else straight_km for j in range(n)] for i in range(n)]
    return BaseMatrix(road_km=road, car_min=car, straight_km=straight, source="osrm")


def test_travel_times_take_public_minutes_from_transit_and_fall_back_otherwise():
    matrix = build_transit_matrix(POINTS, [[0, 42, None], [42, 0, 15], [None, 15, 0]], "13:00")
    travel = TravelTimes(_base(), TravelModel(), TrafficProfile({17: 1.8}), transit=matrix)

    assert travel.minutes(0, 1, Transport.PUBLIC, slot_min=0) == 42
    # 2ГИС не нашёл маршрут: считаем по-старому, 8 км по прямой ×1.3 при 15 км/ч плюс 10 минут ожидания.
    assert travel.minutes(0, 2, Transport.PUBLIC, slot_min=0) == 52
    assert TravelTimes(_base(), TravelModel(), TrafficProfile({})).minutes(0, 1, Transport.PUBLIC, 0) == 52
    # Километры остаются нашей оценкой по прямой, 2ГИС даёт только время.
    assert travel.km(0, 1, Transport.PUBLIC) == pytest.approx(10.4)
    assert travel.minutes(1, 1, Transport.PUBLIC, slot_min=0) == 0
    # Остальной транспорт матрица 2ГИС не трогает.
    assert travel.minutes(0, 1, Transport.CAR, slot_min=17 * 60) == 36
    assert travel.minutes(0, 1, Transport.FOOT, slot_min=0) == 150


def test_make_problem_passes_the_transit_matrix_and_ignores_a_mismatched_one():
    requests = [req("R1", 5, 0, "10:00", "12:00")]
    engineers = [eng("E1", transport=Transport.PUBLIC)]
    points = _points(requests, engineers)
    matrix = build_transit_matrix(points, [[0, 7], [7, 0]], "13:00")

    problem = make_problem(
        requests, engineers, model=TravelModel(), traffic=TrafficProfile({}), transit=matrix
    )
    assert problem.travel.transit is matrix
    assert problem.travel_min(0, 1, engineers[0]) == 7

    other = build_transit_matrix([at(9, 9), at(8, 8)], [[0, 7], [7, 0]], "13:00")
    without = make_problem(
        requests, engineers, model=TravelModel(), traffic=TrafficProfile({}), transit=other
    )
    assert without.travel.transit is None
    assert without.travel_min(0, 1, engineers[0]) > 7


def test_planning_context_passes_the_transit_matrix_into_the_day_problem():
    requests, engineers = day_requests(), day_engineers()
    matrix = build_transit_matrix(_points(requests, engineers), [[0] * 5 for _ in range(5)], "13:00")
    session = new_session(ctx=context(transit=matrix), requests=requests, engineers=engineers)
    assert session.problem.travel.transit is matrix


def test_settings_and_build_deps_take_the_transit_matrix_from_the_file(tmp_path):
    settings = Settings.from_env({"DATA_DIR": str(tmp_path), "GEOCODER": "cache-only"})
    assert settings.transit_matrix_path == tmp_path / "transit_matrix.json"
    assert build_deps(settings).ingest.planning.transit is None

    save_transit_matrix(
        build_transit_matrix(POINTS, [[0, 12, 20], [12, 0, 30], [30, 25, 0]], "13:00"),
        settings.transit_matrix_path,
    )
    assert build_deps(settings).ingest.planning.transit.departure == "13:00"

    settings.transit_matrix_path.write_text("{", encoding="utf-8")
    assert build_deps(settings).ingest.planning.transit is None

    moved = Settings.from_env({"DATA_DIR": str(tmp_path), "TRANSIT_MATRIX_PATH": str(tmp_path / "своя.json")})
    assert moved.transit_matrix_path == tmp_path / "своя.json"
