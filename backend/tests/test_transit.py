"""Время на общественном транспорте от 2ГИС: клиент, файл матрицы и подстановка в планировщик. Всё без сети."""

import json
from datetime import date, datetime, timedelta

import httpx
import pytest

from app.api.deps import build_deps
from app.domain.enums import EventType, Transport
from app.domain.models import Event
from app.geo.haversine import haversine_km
from app.geo.matrix import BaseMatrix, TrafficProfile, TravelModel, TravelTimes
from app.geo.transit import (
    DEFAULT_PAUSE_S,
    DEMO_MAX_DISTANCE_KM,
    ELEMENTS_PER_MINUTE,
    GROUP_DISTANCE_KM,
    MAX_BLOCK,
    PUBLIC_TRANSPORT_KINDS,
    RATE_LIMIT_RETRIES,
    RATE_LIMIT_WAIT_S,
    REQUESTS_PER_MINUTE,
    TRANSIT_REQUEST,
    TransitClient,
    TransitError,
    TransitLookup,
    build_transit_matrix,
    departure_timestamp,
    distance_groups,
    load_transit_matrices,
    load_transit_matrix,
    save_transit_matrix,
    transit_matrix_path,
)
from app.planning.session import apply_event
from app.settings import Settings
from app.solvers.problem import make_problem
from tests.helpers import at, eng, req
from tests.planning_helpers import context, day_engineers, day_requests, new_session

KEY = "демо-ключ-2гис"
POINTS = [at(0, 0), at(3, 0), at(0, 4)]
MANY = [at(k / 2, 0) for k in range(60)]  # 6 блоков по 10 точек в пределах 30 км: одна группа


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
    # Режим — поле transport, виды транспорта обязательны: с полем type 2ГИС отвечает 422 «type is invalid».
    assert body["transport"] == "public_transport" and "type" not in body
    assert body["public_transport_params"] == {"transport": list(PUBLIC_TRANSPORT_KINDS)}
    assert {"bus", "metro", "tram"} <= set(PUBLIC_TRANSPORT_KINDS)
    # RFC 3339 со смещением Москвы: без смещения 2ГИС отвечает 400 «'start_time' has non-RFC3339 form».
    stamp = datetime.fromisoformat(body[TRANSIT_REQUEST["departure"]])
    assert body[TRANSIT_REQUEST["departure"]].endswith("T13:00:00+03:00")
    assert stamp.utcoffset() == timedelta(hours=3)
    assert minutes[0][1] == 10 and minutes[0][0] == 0


def test_departure_is_the_next_monday_in_the_future_with_the_moscow_offset():
    # 16.09.2026 — среда: ближайший понедельник 21.09, день выгрузки 17.08.2026 тоже понедельник.
    assert departure_timestamp("13:00", today=date(2026, 9, 16)) == "2026-09-21T13:00:00+03:00"
    # В сам понедельник берётся следующий: расписание на уже идущий день может быть неполным.
    assert departure_timestamp("09:30", today=date(2026, 9, 21)) == "2026-09-28T09:30:00+03:00"
    assert datetime.fromisoformat(departure_timestamp("13:00")).date() > date.today()


def test_points_are_split_into_blocks_of_10_with_a_call_per_block_pair():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append((len(body[TRANSIT_REQUEST["sources"]]), len(body[TRANSIT_REQUEST["targets"]])))
        assert len(body[TRANSIT_REQUEST["points"]]) <= 2 * MAX_BLOCK
        return _ok(body)

    minutes = _client(handler).matrix(MANY, "13:00")

    # Демо-ключ 2ГИС принимает матрицу не больше 10×10: «permissible dimension of the matrix is exceeded».
    assert MAX_BLOCK == 10
    assert len(calls) == 36
    assert all(sources <= 10 and targets <= 10 for sources, targets in calls)
    assert sum(sources * targets for sources, targets in calls) == 60 * 60
    assert len(minutes) == 60 and all(len(row) == 60 for row in minutes)
    assert minutes[59][0] == 10 and minutes[0][59] == 10 and minutes[30][31] == 10


def test_points_farther_than_the_demo_limit_are_never_in_one_request():
    # Москва и Кашира: 12 точек рядом с офисом и 3 точки в 70 км к югу, как в Юго-востоке.
    city = [at(k / 2, 0) for k in range(12)]
    far = [at(0, -70), at(2, -70), at(0, -72)]
    points = city + far
    calls = []

    def handler(request):
        body = json.loads(request.content)
        pts = [
            (p[TRANSIT_REQUEST["lat"]], p[TRANSIT_REQUEST["lon"]]) for p in body[TRANSIT_REQUEST["points"]]
        ]
        calls.append(len(pts))
        # Демо-ключ отвечает 403 «excessive distance between points for demo-keys, max (km): 50».
        assert all(haversine_km(*a, *b) < DEMO_MAX_DISTANCE_KM for a in pts for b in pts)
        return _ok(body)

    minutes = _client(handler).matrix(points, "13:00")

    assert distance_groups(points) == [list(range(12)), [12, 13, 14]]
    # Город — 2 блока, 4 запроса; Кашира — 1 запрос; пар между ними 2ГИС не считает.
    assert len(calls) == 5
    assert minutes[0][11] == 10 and minutes[12][14] == 10 and minutes[13][13] == 0
    assert minutes[0][12] is None and minutes[14][3] is None


def test_distance_groups_keep_every_pair_within_the_limit_even_along_a_chain():
    # Цепочка через каждые 30 км: соседи близко, но первая и третья точки в 60 км, в одну группу им нельзя.
    chain = [at(0, 0), at(30, 0), at(60, 0)]
    groups = distance_groups(chain)
    assert groups == [[0, 1], [2]]
    assert all(
        haversine_km(*chain[a], *chain[b]) <= GROUP_DISTANCE_KM
        for group in groups
        for a in group
        for b in group
    )
    assert GROUP_DISTANCE_KM < DEMO_MAX_DISTANCE_KM == 50


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

    # В любой минуте, в том числе календарной, запросов и элементов не больше минутных лимитов ключа.
    worst_requests = int(60 // DEFAULT_PAUSE_S) + 1
    assert worst_requests <= REQUESTS_PER_MINUTE
    assert worst_requests * MAX_BLOCK * MAX_BLOCK <= ELEMENTS_PER_MINUTE
    assert TransitClient(KEY).pause_s == DEFAULT_PAUSE_S
    # 30 точек: 3 блока, 9 запросов и 8 пауз между ними, перед первым запросом паузы нет.
    assert pauses == [0.5] * 8
    assert steps == [(done, 9) for done in range(1, 10)]


def test_rate_limit_answer_waits_and_retries_the_same_request():
    answers = [httpx.Response(429, text="Too Many Requests")]
    pauses = []

    def handler(request):
        return answers.pop(0) if answers else _ok(json.loads(request.content))

    client = TransitClient(
        KEY, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=pauses.append
    )
    minutes = client.matrix(POINTS, "13:00")

    # Минутная остановка ключа не рушит регион: ждём и повторяем тот же запрос.
    assert pauses == [RATE_LIMIT_WAIT_S] and minutes[0][1] == 10


def test_rate_limit_that_does_not_pass_becomes_transit_error():
    def handler(request):
        return httpx.Response(429, text="Too Many Requests")

    pauses = []
    client = TransitClient(
        KEY, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=pauses.append
    )
    with pytest.raises(TransitError, match="429"):
        client.matrix(POINTS, "13:00")
    assert pauses == [RATE_LIMIT_WAIT_S] * RATE_LIMIT_RETRIES


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


def test_transit_matrix_round_trip_keeps_six_decimals_and_the_region(tmp_path):
    matrix = build_transit_matrix(POINTS, [[0, 12, None], [12, 0, 30], [30, 25, 0]], "13:00", region="east")
    path = transit_matrix_path(tmp_path, "east")

    save_transit_matrix(matrix, path)

    assert path == tmp_path / "east.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source"] == "2gis" and payload["departure"] == "13:00"
    assert payload["region"] == "east"
    assert payload["points"] == [[round(lat, 6), round(lon, 6)] for lat, lon in POINTS]
    assert payload["minutes"][0][2] is None
    loaded = load_transit_matrix(path)
    assert loaded == matrix and loaded.matches(POINTS) and loaded.region == "east"


def test_load_transit_matrices_reads_every_region_and_skips_what_it_cannot_read(tmp_path):
    assert load_transit_matrices(tmp_path / "нет-каталога") == []

    directory = tmp_path / "transit"
    save_transit_matrix(
        build_transit_matrix(POINTS, [[0, 12, 20], [12, 0, 30], [30, 25, 0]], "13:00", region="east"),
        transit_matrix_path(directory, "east"),
    )
    save_transit_matrix(
        build_transit_matrix(POINTS[:2], [[0, 5], [5, 0]], "09:00", region="north_west"),
        transit_matrix_path(directory, "north_west"),
    )
    (directory / "broken.json").write_text("{не json", encoding="utf-8")
    (directory / "заметка.txt").write_text("это не матрица", encoding="utf-8")

    loaded = load_transit_matrices(directory)
    assert [matrix.region for matrix in loaded] == ["east", "north_west"]
    assert loaded[0].matches(POINTS) and loaded[1].departure == "09:00"


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
    travel = TravelTimes(
        _base(), TravelModel(), TrafficProfile({17: 1.8}), transit=TransitLookup(POINTS, [matrix])
    )

    assert travel.minutes(0, 1, Transport.PUBLIC, slot_min=0) == 42
    # 2ГИС не нашёл маршрут: встроенная модель, 8 км по прямой — поездка 22.5 + 2.8·8 = 44.9 минуты.
    assert travel.minutes(0, 2, Transport.PUBLIC, slot_min=0) == 45
    assert TravelTimes(_base(), TravelModel(), TrafficProfile({})).minutes(0, 1, Transport.PUBLIC, 0) == 45
    # Километры остаются нашей оценкой по прямой, 2ГИС даёт только время.
    assert travel.km(0, 1, Transport.PUBLIC) == pytest.approx(10.4)
    assert travel.minutes(1, 1, Transport.PUBLIC, slot_min=0) == 0
    # Остальной транспорт матрица 2ГИС не трогает.
    assert travel.minutes(0, 1, Transport.CAR, slot_min=17 * 60) == 36
    assert travel.minutes(0, 1, Transport.BIKE, slot_min=0) == 32


EAST = [[0, 12, 20], [13, 0, 30], [21, 31, 0]]


def test_lookup_finds_points_by_coordinates_so_a_new_point_does_not_hide_the_known_pairs():
    matrix = build_transit_matrix(POINTS, EAST, "13:00", region="east")
    new = at(9, 9)

    lookup = TransitLookup([*POINTS, new], [matrix])
    assert [lookup.minutes(0, 1), lookup.minutes(1, 0), lookup.minutes(2, 1)] == [12, 13, 31]
    assert lookup.minutes(0, 3) is None and lookup.minutes(3, 0) is None and lookup.minutes(3, 3) is None
    assert lookup.covered == 6

    # Порядок и состав точек задачи не важны: пара узнаётся по координатам.
    reordered = TransitLookup([POINTS[2], new, POINTS[0]], [matrix])
    assert reordered.minutes(0, 2) == 21 and reordered.minutes(2, 0) == 20
    assert reordered.minutes(0, 1) is None and reordered.covered == 2
    # Координаты сравниваются с округлением до 6 знаков, как в файле.
    lat, lon = POINTS[1]
    assert TransitLookup([POINTS[0], (lat + 3e-8, lon - 3e-8)], [matrix]).minutes(0, 1) == 12
    assert TransitLookup([new, at(10, 10)], [matrix]).covered == 0


def test_lookup_takes_each_pair_from_its_own_matrix_and_leaves_pairs_across_matrices_to_the_model():
    east = build_transit_matrix(POINTS, EAST, "13:00", region="east")
    far = [at(20, 0), at(21, 0)]
    north_west = build_transit_matrix(far, [[0, 5], [6, 0]], "13:00", region="north_west")

    lookup = TransitLookup([POINTS[0], far[1], POINTS[1], far[0]], [east, north_west])
    assert lookup.minutes(0, 2) == 12 and lookup.minutes(2, 0) == 13
    assert lookup.minutes(1, 3) == 6 and lookup.minutes(3, 1) == 5
    assert lookup.minutes(0, 1) is None and lookup.minutes(3, 2) is None
    assert lookup.covered == 4


def test_lookup_tries_every_place_of_a_point_that_repeats_in_the_file():
    # Старт инженера по адресу его заявки: точка в файле дважды, и у первого места нет маршрута ко второй точке.
    points = [POINTS[0], POINTS[1], POINTS[0]]
    matrix = build_transit_matrix(points, [[0, 12, 0], [None, 0, 14], [0, 15, 0]], "13:00")

    lookup = TransitLookup([POINTS[1], POINTS[0]], [matrix])
    assert lookup.minutes(0, 1) == 14 and lookup.minutes(1, 0) == 12
    assert lookup.covered == 2


def test_lookup_leaves_a_pair_without_a_route_to_the_model():
    matrix = build_transit_matrix(POINTS, [[0, None, 20], [13, 0, 30], [21, 31, 0]], "13:00")
    lookup = TransitLookup(POINTS, [matrix])
    assert lookup.minutes(0, 1) is None and lookup.minutes(1, 0) == 13

    base = BaseMatrix(
        road_km=[[0.0, 4.0, 4.0], [4.0, 0.0, 4.0], [4.0, 4.0, 0.0]],
        car_min=[[0.0, 9.0, 9.0], [9.0, 0.0, 9.0], [9.0, 9.0, 0.0]],
        straight_km=[[0.0, 5.0, 5.0], [5.0, 0.0, 5.0], [5.0, 5.0, 0.0]],
        source="osrm",
    )
    travel = TravelTimes(base, TravelModel(), TrafficProfile({}), transit=lookup)
    # 5 км по прямой: пешком 78 минут, поездка 22.5 + 14 = 36.5.
    assert travel.minutes(0, 1, Transport.PUBLIC, slot_min=0) == 37
    assert travel.minutes(1, 0, Transport.PUBLIC, slot_min=0) == 13


def test_make_problem_takes_known_pairs_from_2gis_and_pairs_with_a_new_point_from_the_model():
    requests = [req("R1", 5, 0, "10:00", "12:00"), req("R2", 0, 5, "10:00", "12:00")]
    engineers = [eng("E1", transport=Transport.PUBLIC)]
    engineer = engineers[0]
    matrix = build_transit_matrix(
        _points(requests, engineers), [[0, 7, 9], [7, 0, 11], [9, 11, 0]], "13:00", region="east"
    )
    other = build_transit_matrix([at(40, 40), at(41, 41)], [[0, 7], [7, 0]], "13:00", region="north_west")

    def problem_with(day_requests, transit):
        return make_problem(
            day_requests, engineers, model=TravelModel(), traffic=TrafficProfile({}), transit=transit
        )

    problem = problem_with(requests, [other, matrix])
    assert problem.travel_min(0, 1, engineer) == 7 and problem.travel_min(1, 2, engineer) == 11
    assert problem.travel.transit.covered == 6

    # Срочная заявка в 3 км к западу от офиса: пары прежних точек остаются из 2ГИС, пары с ней считает модель.
    urgent = problem_with([*requests, req("U1", -3, 0, "10:00", "12:00")], [other, matrix])
    new, r1, r2 = (urgent.request_node(request_id) for request_id in ("U1", "R1", "R2"))
    assert urgent.travel_min(0, r1, engineer) == 7 and urgent.travel_min(r1, r2, engineer) == 11
    # 3 км: поездка 22.5 + 2.8·3 = 30.9 минуты; от R1 8 км: 22.5 + 22.4 = 44.9.
    assert urgent.travel_min(0, new, engineer) == 31 and urgent.travel_min(new, 0, engineer) == 31
    assert urgent.travel_min(r1, new, engineer) == 45

    # Ни одной пары дня нет в матрицах — день считается встроенной моделью, как без 2ГИС.
    without = problem_with(requests, [other])
    assert without.travel.transit is None
    assert without.travel_min(0, 1, engineer) > 7
    assert problem_with(requests, []).travel.transit is None
    assert problem_with(requests, ()).travel.transit is None


def test_urgent_event_keeps_2gis_minutes_between_the_points_already_in_the_day():
    requests, engineers = day_requests(), day_engineers()
    points = _points(requests, engineers)
    size = len(points)
    minutes = [[0 if i == j else 50 + 10 * i + j for j in range(size)] for i in range(size)]
    other = build_transit_matrix(POINTS, [[0] * 3 for _ in range(3)], "13:00", region="north_west")
    matrix = build_transit_matrix(points, minutes, "13:00", region="east")

    ctx = context(transit=[other, matrix])
    session = new_session(ctx=ctx, requests=requests, engineers=engineers)
    travel = session.problem.travel
    assert travel.minutes(2, 4, Transport.PUBLIC, 0) == 74
    assert context().transit == ()
    assert new_session(requests=requests, engineers=engineers).problem.travel.transit is None

    urgent = req("U1", 0, -3, "13:00", "15:00", duration=60)
    updated = apply_event(session, Event(type=EventType.URGENT, time="13:00", request=urgent), ctx)
    problem = updated.problem
    r1, r3, new = (problem.request_node(request_id) for request_id in ("R1", "R3", "U1"))
    assert problem.travel.minutes(r1, r3, Transport.PUBLIC, 0) == 74
    assert problem.travel.minutes(0, r1, Transport.PUBLIC, 0) == 52
    # 3 км к югу от офиса: поездка 22.5 + 2.8·3 = 30.9 минуты.
    assert problem.travel.minutes(0, new, Transport.PUBLIC, 0) == 31


def test_settings_and_build_deps_take_the_matrices_from_the_directory(tmp_path):
    settings = Settings.from_env({"DATA_DIR": str(tmp_path), "GEOCODER": "cache-only"})
    assert settings.transit_dir == tmp_path / "transit"
    assert list(build_deps(settings).ingest.planning.transit) == []

    save_transit_matrix(
        build_transit_matrix(POINTS, [[0, 12, 20], [12, 0, 30], [30, 25, 0]], "13:00", region="east"),
        transit_matrix_path(settings.transit_dir, "east"),
    )
    save_transit_matrix(
        build_transit_matrix(POINTS[:2], [[0, 5], [5, 0]], "09:00", region="north_west"),
        transit_matrix_path(settings.transit_dir, "north_west"),
    )
    (settings.transit_dir / "east.json.bak").write_text("{", encoding="utf-8")
    (settings.transit_dir / "broken.json").write_text("{", encoding="utf-8")

    loaded = build_deps(settings).ingest.planning.transit
    assert [matrix.region for matrix in loaded] == ["east", "north_west"]
    assert loaded[0].departure == "13:00"

    moved = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "TRANSIT_MATRIX_DIR": str(tmp_path / "свой-каталог")}
    )
    assert moved.transit_dir == tmp_path / "свой-каталог"
