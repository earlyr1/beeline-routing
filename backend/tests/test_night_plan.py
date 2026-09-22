"""Ночной план: отпечаток задачи, выбор утреннего плана при сборке дня, API и scripts/night_plan.py. Без сети."""

import json
import subprocess
import sys
from dataclasses import replace

import httpx
import pytest

from app.api.deps import planning_context
from app.domain.enums import EventType, Priority, RequestStatus, RequestTier, Skill, Transport
from app.domain.models import Event
from app.domain.timeutil import fmt_hhmm
from app.geo.matrix import TrafficProfile, TravelModel
from app.geo.transit import build_transit_matrix
from app.ingest.bundle import save_bundle
from app.planning import session as session_module
from app.planning.models import PrecomputedPlan
from app.planning.night import (
    NightMetrics,
    NightPlan,
    load_night_plan,
    night_plan_path,
    plan_routes,
    problem_fingerprint,
    save_night_plan,
)
from app.planning.session import apply_event, day_problem, start_session
from app.planning.workload import DEFAULT_WORKLOAD_LEVEL, workload_weights
from app.settings import BACKEND_DIR, Settings
from app.solvers.assemble import build_plan
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import OrToolsSolver
from app.solvers.portfolio import PORTFOLIO, SolverPool, plan_cost
from scripts import night_plan as cli
from tests.api_helpers import csv_bytes, prepared_bundle, sample_bundle, upload
from tests.helpers import at, eng
from tests.planning_helpers import (
    EXACT_TRAVEL_LEVEL,
    OFFICE,
    context,
    day_engineers,
    day_requests,
    new_session,
    routes,
)

REGION = "t"
COMPUTED_AT = "2026-09-21T03:10:00+03:00"
# Допустимый план дня, которого солвер сам не выберет: всё у второй бригады. По нему видно, что утренний план
# взят из файла, а не найден поиском.
SECOND_CREW = {"E2": ["R1", "R2", "R3"]}
# R1 после R3: начало R1 позже его окна до 12:00 — маршрут нарушает ограничения.
LATE_ORDER = {"E1": ["R3", "R1", "R2"]}


def write_night_plan(
    directory,
    ctx,
    requests=None,
    engineers=None,
    *,
    night_routes=None,
    fingerprint=None,
    level=DEFAULT_WORKLOAD_LEVEL,
    region=REGION,
):
    """Ночной план дня в каталоге directory, как его пишет scripts/night_plan.py. Отпечаток — по задаче дня."""
    problem = day_problem(requests or day_requests(), engineers or day_engineers(), ctx, level, True)
    weights = workload_weights(level)
    if night_routes is None:
        plan = OrToolsSolver(time_limit_s=1, weights=weights).solve(problem)
        night_routes = plan_routes(plan)
    else:
        plan = build_plan(problem, "ortools", night_routes)
    night = NightPlan(
        fingerprint=fingerprint or problem_fingerprint(problem, weights),
        region=region,
        time_limit_s=7200,
        search_s=7200,
        workers=4,
        computed_at=COMPUTED_AT,
        workload_level=level,
        lunch_enabled=True,
        cost=plan_cost(problem, plan, weights),
        metrics=NightMetrics(
            engineers_used=plan.metrics.engineers_used,
            total_km=plan.metrics.total_km,
            assigned=plan.metrics.assigned,
            unassigned=plan.metrics.unassigned,
        ),
        routes=night_routes,
    )
    save_night_plan(night, night_plan_path(directory, region))
    return night


def patch_night_file(directory, **fields):
    """Правит поля записанного ночного плана прямо в файле: так в него попадают маршруты, которых нет в дне."""
    path = night_plan_path(directory, REGION)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(fields)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


class Searches(list):
    """Вызовы поиска при сборке дня: план, от которого стартовал поиск (seed), или None."""

    def __init__(self, monkeypatch, forbid=False):
        super().__init__()
        real = session_module.search_plan

        def spy(problem, weights, time_limit_s, pool=None, *, share=1, seed=None):
            if forbid:
                raise AssertionError("поиск не должен запускаться: утренний план взят из ночного файла")
            self.append(seed)
            return real(problem, weights, time_limit_s, pool, share=share, seed=seed)

        monkeypatch.setattr(session_module, "search_plan", spy)


# --- отпечаток задачи ---


def mixed_engineers():
    """Бригады на всех трёх транспортах: отпечаток покрывает минуты и километры каждого."""
    return [eng("E1"), eng("E2", transport=Transport.PUBLIC), eng("E3", transport=Transport.BIKE)]


def fingerprint(
    requests=None, engineers=None, *, ctx=None, level=DEFAULT_WORKLOAD_LEVEL, lunch=True, weights=None
):
    problem = day_problem(
        requests or day_requests(), engineers or mixed_engineers(), ctx or context(), level, lunch
    )
    return problem_fingerprint(problem, weights or workload_weights(level))


def changed_request(index, **update):
    requests = day_requests()
    requests[index] = requests[index].model_copy(update=update)
    return requests


def changed_engineer(index, **update):
    engineers = mixed_engineers()
    engineers[index] = engineers[index].model_copy(update=update)
    return engineers


class TableOsrm:
    """OSRM без сети: километры и минуты по номеру точек, scale меняет все километры."""

    def __init__(self, scale=1.0):
        self.scale = scale

    def table(self, points):
        size = len(points)
        km = [[abs(i - j) * self.scale for j in range(size)] for i in range(size)]
        minutes = [[abs(i - j) * 3.0 for j in range(size)] for i in range(size)]
        return km, minutes


def transit_ctx(minutes):
    """Контекст с матрицей 2ГИС на точках дня: у всех пар одинаковые минуты."""
    points = [(e.start_lat, e.start_lon) for e in mixed_engineers()] + [
        (r.lat, r.lon) for r in day_requests()
    ]
    size = len(points)
    matrix = [[0 if i == j else minutes for j in range(size)] for i in range(size)]
    return context(transit=[build_transit_matrix(points, matrix, "13:00", REGION)])


def test_fingerprint_is_the_same_for_two_independent_builds_and_any_input_order():
    first = fingerprint()
    # Заявки и инженеры заново из JSON, в обратном порядке: номера узлов задачи другие, отпечаток тот же.
    requests = [type(r).model_validate_json(r.model_dump_json()) for r in reversed(day_requests())]
    engineers = [type(e).model_validate_json(e.model_dump_json()) for e in reversed(mixed_engineers())]
    assert fingerprint(requests, engineers) == first
    assert len(first) == 64 and int(first, 16) >= 0


def test_fingerprint_is_the_same_in_another_process():
    # Другой процесс с другой солью hash(): отпечаток не зависит ни от неё, ни от порядка множеств.
    code = "from tests.test_night_plan import fingerprint; print(fingerprint())"
    other = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env={"PYTHONHASHSEED": "12345", "PATH": ""},
        capture_output=True,
        text=True,
        check=True,
    )
    assert other.stdout.strip() == fingerprint()


@pytest.mark.parametrize(
    "change",
    [
        {"requests": changed_request(0, duration_min=31)},
        {"requests": changed_request(0, window_start=601)},
        {"requests": changed_request(0, window_end=719)},
        {"requests": changed_request(0, priority=Priority.URGENT)},
        {"requests": changed_request(0, tier=RequestTier.EMERGENCY)},
        {"requests": changed_request(0, asap=True)},
        {"requests": changed_request(0, skill=Skill.CONNECTION)},
        {"requests": changed_request(0, transport_required=Transport.CAR)},
        {"requests": changed_request(0, status=RequestStatus.CANCELLED)},
        {"requests": changed_request(0, needs_equipment=True)},
        {"requests": changed_request(0, fixed_engineer_id="E2")},
        {"requests": changed_request(0, lat=at(1, 0.1)[0])},
        {"requests": changed_request(0, lat=None, lon=None)},
        {"requests": day_requests()[:2]},
        {"engineers": changed_engineer(0, shift_start=541)},
        {"engineers": changed_engineer(0, shift_end=1079)},
        {"engineers": changed_engineer(0, skills=[Skill.LOCAL])},
        {"engineers": changed_engineer(0, transport=Transport.BIKE)},
        {"engineers": changed_engineer(0, available=False, unavailable_from=720)},
        {"engineers": changed_engineer(0, equipment_stock=5)},
        {"engineers": changed_engineer(0, start_lat=at(0, 0.5)[0])},
        {"engineers": mixed_engineers()[:2]},
        {"lunch": False},
        {"level": 0},
        {"level": EXACT_TRAVEL_LEVEL},
        {"weights": replace(workload_weights(DEFAULT_WORKLOAD_LEVEL), reassignment=1)},
        {"ctx": context(osrm=TableOsrm())},
        {"ctx": context(traffic=TrafficProfile({10: 1.5}))},
        {"ctx": context(model=TravelModel(public_ride_base_min=20.0))},
        {"ctx": context(model=TravelModel(bike_leg_limit_km=14.0))},
        {"ctx": transit_ctx(20)},
    ],
    ids=lambda change: next(iter(change)),
)
def test_any_single_relevant_change_gives_another_fingerprint(change):
    assert fingerprint(**change) != fingerprint()


def test_another_osrm_or_another_2gis_file_gives_another_fingerprint():
    assert fingerprint(ctx=context(osrm=TableOsrm(1.0))) != fingerprint(ctx=context(osrm=TableOsrm(1.001)))
    assert fingerprint(ctx=transit_ctx(20)) != fingerprint(ctx=transit_ctx(21))
    assert fingerprint(ctx=transit_ctx(20)) == fingerprint(ctx=transit_ctx(20))


@pytest.mark.parametrize(
    "change",
    [
        {"requests": changed_request(0, address="другой адрес")},
        {"requests": changed_request(0, district="Таганский")},
        {"requests": changed_request(0, source_type_bk="Локальная заявка", source_type_hd="Нет линка")},
        {"requests": changed_request(0, geocode_precision="street")},
        {"engineers": changed_engineer(0, name="Другая бригада")},
    ],
    ids=lambda change: next(iter(change)),
)
def test_fields_the_solvers_do_not_read_do_not_change_the_fingerprint(change):
    assert fingerprint(**change) == fingerprint()


# --- выбор утреннего плана при сборке дня ---


def test_matching_night_plan_is_the_morning_plan_without_search(tmp_path, monkeypatch, caplog):
    ctx = context(night_plan_dir=tmp_path)
    write_night_plan(tmp_path, ctx, night_routes=SECOND_CREW)
    Searches(monkeypatch, forbid=True)

    with caplog.at_level("INFO", logger="app.planning.night"):
        session = new_session(ctx)

    assert routes(session.plan) == {"E1": [], **SECOND_CREW}
    assert session.plan.solver == "ortools" and session.plan.violations == []
    assert session.precomputed == PrecomputedPlan(search_minutes=120, computed_at=COMPUTED_AT)
    # FCFS считается как обычно.
    assert session.baseline.solver == "fcfs" and session.baseline == FcfsSolver().solve(session.problem)
    assert "взят утренним планом без поиска" in caplog.text


def test_events_replan_from_the_night_plan_and_keep_its_origin(tmp_path):
    ctx = context(night_plan_dir=tmp_path)
    write_night_plan(tmp_path, ctx, night_routes=SECOND_CREW)
    session = new_session(ctx)

    replanned = apply_event(session, Event(type=EventType.CANCEL, time="13:00", request_id="R2"), ctx)

    # R1 уже сделан по ночному плану: визит закреплён за второй бригадой.
    assert routes(replanned.plan)["E2"][0] == "R1" and replanned.plan.routes[1].visits[0].pinned
    assert "R2" not in [rid for sequence in routes(replanned.plan).values() for rid in sequence]
    assert replanned.previous_plan == session.plan
    assert replanned.precomputed == session.precomputed


def test_night_plan_of_another_workload_level_seeds_the_live_search(tmp_path, monkeypatch, caplog):
    ctx = context(night_plan_dir=tmp_path)
    write_night_plan(tmp_path, ctx, night_routes=SECOND_CREW)
    searches = Searches(monkeypatch)

    with caplog.at_level("INFO", logger="app.planning.night"):
        session = new_session(ctx, workload_level=EXACT_TRAVEL_LEVEL)

    assert session.precomputed is None
    assert len(searches) == 1 and routes(searches[0]) == {"E1": [], **SECOND_CREW}
    # Поиск от маршрутов ночного плана не хуже их по цели при весах нового уровня.
    weights = workload_weights(EXACT_TRAVEL_LEVEL)
    assert plan_cost(session.problem, session.plan, weights) <= plan_cost(
        session.problem, searches[0], weights
    )
    assert "отпечаток задачи другой" in caplog.text and "поиск стартует от его маршрутов" in caplog.text


def test_night_plan_of_another_day_with_invalid_routes_is_ignored(tmp_path, monkeypatch, caplog):
    ctx = context(night_plan_dir=tmp_path)
    write_night_plan(tmp_path, ctx)
    patch_night_file(tmp_path, routes={"E1": ["R1", "X9"]}, fingerprint="0" * 64)
    searches = Searches(monkeypatch)

    with caplog.at_level("INFO", logger="app.planning.night"):
        session = new_session(ctx)

    assert session.precomputed is None and searches == [None]
    assert "заявки X9 нет среди открытых заявок дня" in caplog.text


def test_night_plan_that_breaks_a_constraint_is_rejected_even_with_the_same_fingerprint(
    tmp_path, monkeypatch, caplog
):
    ctx = context(night_plan_dir=tmp_path)
    write_night_plan(tmp_path, ctx, night_routes=LATE_ORDER)
    searches = Searches(monkeypatch)

    with caplog.at_level("INFO", logger="app.planning.night"):
        session = new_session(ctx)

    assert session.precomputed is None and searches == [None]
    assert session.plan.violations == []
    assert "отпечаток совпал, но маршруты не проходят проверку" in caplog.text and "позже окна" in caplog.text


@pytest.mark.parametrize(
    "night_routes, reason",
    [
        ({"E9": ["R1"]}, "бригады E9 нет в дне"),
        ({"E1": ["R1"], "E2": ["R1"]}, "заявка R1 стоит в маршрутах дважды"),
    ],
)
def test_night_plan_with_foreign_routes_is_rejected(tmp_path, monkeypatch, caplog, night_routes, reason):
    ctx = context(night_plan_dir=tmp_path)
    problem = day_problem(day_requests(), day_engineers(), ctx, DEFAULT_WORKLOAD_LEVEL, True)
    write_night_plan(tmp_path, ctx, night_routes={"E1": ["R1"]})
    data = patch_night_file(tmp_path, routes=night_routes)
    searches = Searches(monkeypatch)

    with caplog.at_level("INFO", logger="app.planning.night"):
        session = new_session(ctx)

    assert data["fingerprint"] == problem_fingerprint(problem, workload_weights(DEFAULT_WORKLOAD_LEVEL))
    assert session.precomputed is None and searches == [None]
    assert reason in caplog.text


@pytest.mark.parametrize(
    "content",
    [b'{"fingerprint": "abc"}', b'{"fingerprint": "\xff\xfe broken'],
    ids=["not a night plan", "not utf-8"],
)
def test_broken_night_plan_file_falls_back_to_the_live_search(tmp_path, monkeypatch, caplog, content):
    ctx = context(night_plan_dir=tmp_path)
    path = night_plan_path(tmp_path, REGION)
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    searches = Searches(monkeypatch)

    with caplog.at_level("INFO", logger="app.planning.night"):
        session = new_session(ctx)

    assert session.precomputed is None and searches == [None]
    assert "не читается" in caplog.text


def test_no_night_plan_file_is_a_live_search(tmp_path, monkeypatch, caplog):
    searches = Searches(monkeypatch)
    with caplog.at_level("INFO", logger="app.planning.night"):
        session = new_session(context(night_plan_dir=tmp_path))
    assert session.precomputed is None and searches == [None]
    assert "файла" in caplog.text and "нет" in caplog.text


@pytest.mark.parametrize("region", ["../t", "/etc/t", "..", "", "t\x00"])
def test_region_of_an_uploaded_bundle_does_not_lead_the_night_plan_lookup_astray(
    tmp_path, monkeypatch, caplog, region
):
    # Регион загруженного бандла — строка из файла: каталог ночных планов он не покидает и загрузку не роняет.
    ctx = context(night_plan_dir=tmp_path / "bundles")
    write_night_plan(tmp_path, ctx, night_routes=SECOND_CREW)
    searches = Searches(monkeypatch)

    with caplog.at_level("INFO", logger="app.planning.night"):
        session = start_session("d_test", region, OFFICE, day_requests(), day_engineers(), None, ctx)

    assert session.precomputed is None and searches == [None]
    assert "не ищется: регион не имя каталога" in caplog.text


def test_error_in_the_night_plan_check_does_not_stop_the_day(tmp_path, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("сбой проверки")

    monkeypatch.setattr(session_module, "choose_night_plan", broken)
    searches = Searches(monkeypatch)

    with caplog.at_level("INFO", logger="app.planning.session"):
        session = new_session(context(night_plan_dir=tmp_path))

    assert session.precomputed is None and searches == [None]
    assert "не проверен из-за ошибки" in caplog.text and "сбой проверки" in caplog.text


def test_service_log_shows_why_the_night_plan_was_taken_or_not(tmp_path):
    # Как в контейнере: uvicorn настраивает только свои логгеры и потом импортирует app.api.main. Строки INFO
    # из app.* без обработчика пропали бы: docs/demo.md ищет их в `docker compose logs backend`.
    code = (
        "import logging, logging.config\n"
        "from uvicorn.config import LOGGING_CONFIG\n"
        "logging.config.dictConfig(LOGGING_CONFIG)\n"
        "import app.api.main\n"
        "logging.getLogger('app.planning.night').info('Ночной план региона t не подошёл: файла нет')\n"
    )
    env = {
        "DATA_DIR": str(tmp_path),
        "SOLVER_WORKERS": "1",
        "GEOCODER": "cache-only",
        "PYTHONIOENCODING": "utf-8",
        "PATH": "",
    }
    run = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=120,
    )
    assert "Ночной план региона t не подошёл: файла нет" in run.stderr


def test_pool_starts_only_the_first_strategy_from_the_night_routes(monkeypatch):
    problem = new_session(context()).problem
    submitted = []

    class Done:
        def result(self, timeout=None):
            return {}

    class FakeExecutor:
        def submit(self, fn, problem_, weights_, limit, strategy):
            submitted.append(problem_.previous_order)
            return Done()

    pool = SolverPool(4)
    monkeypatch.setattr(pool, "_pool", lambda: FakeExecutor())
    pool.solve(problem, workload_weights(1), 1, PORTFOLIO, start=SECOND_CREW)
    assert submitted == [SECOND_CREW, {}, {}, {}]
    submitted.clear()
    pool.solve(problem, workload_weights(1), 1, PORTFOLIO)
    assert submitted == [{}, {}, {}, {}]


# --- API ---


def test_state_says_where_the_morning_plan_came_from(api, tmp_path):
    client, deps = api()
    bundle = sample_bundle()
    write_night_plan(
        tmp_path / "bundles",
        deps.ingest.planning,
        bundle.requests,
        bundle.engineers,
        night_routes=SECOND_CREW,
    )
    dataset_id = upload(client, "bundle.json", bundle.model_dump_json().encode())
    base = f"/api/datasets/{dataset_id}"

    state = client.get(f"{base}/state").json()
    precomputed = {"search_minutes": 120.0, "computed_at": COMPUTED_AT}
    assert state["precomputed"] == precomputed
    assert {r["engineer_id"]: [v["request_id"] for v in r["visits"]] for r in state["plan"]["routes"]} == {
        "E1": [],
        **SECOND_CREW,
    }

    # Событие пересчитывается от ночного плана, происхождение утреннего плана остаётся.
    after = client.post(f"{base}/events", json={"type": "cancel", "time": "13:00", "request_id": "R2"})
    assert after.status_code == 200 and after.json()["precomputed"] == precomputed
    assert client.delete(f"{base}/timeline").json()["precomputed"] == precomputed

    # Другая нагрузка — другой отпечаток: план ищется при загрузке, и пометки нет.
    other = client.post(f"{base}/plan", json={"workload_level": 0}).json()
    assert other["workload_level"] == 0 and other["precomputed"] is None
    back = client.post(f"{base}/plan", json={"workload_level": DEFAULT_WORKLOAD_LEVEL}).json()
    assert back["precomputed"] == precomputed


def test_scenario_day_takes_the_night_plan_of_its_region(api, tmp_path):
    """Подготовленный регион идёт тем же путём, что и загрузка файла, поэтому и ночной план подхватывает так же."""
    bundle = prepared_bundle("east", "Восток")
    client, deps = api(bundle=bundle)
    write_night_plan(
        tmp_path / "bundles",
        deps.ingest.planning,
        bundle.requests,
        bundle.engineers,
        night_routes=SECOND_CREW,
        region="east",
    )

    dataset_id = client.post("/api/scenarios/east").json()["dataset_id"]
    state = client.get(f"/api/datasets/{dataset_id}/state").json()

    assert state["precomputed"] == {"search_minutes": 120.0, "computed_at": COMPUTED_AT}
    assert {r["engineer_id"]: [v["request_id"] for v in r["visits"]] for r in state["plan"]["routes"]} == {
        "E1": [],
        **SECOND_CREW,
    }


def test_state_without_night_plan_has_no_precomputed(api):
    client, _ = api()
    dataset_id = upload(client, "bundle.json", sample_bundle().model_dump_json().encode())
    assert client.get(f"/api/datasets/{dataset_id}/state").json()["precomputed"] is None


def test_raw_csv_of_a_prepared_region_has_the_fingerprint_of_its_bundle(api, tmp_path):
    client, deps = api()
    bundle = sample_bundle()
    write_night_plan(tmp_path / "bundles", deps.ingest.planning, bundle.requests, bundle.engineers)
    from_bundle = upload(client, "bundle.json", bundle.model_dump_json().encode())
    # Нетронутая выгрузка региона: и номера заявок, и окна те же, что в бандле, — иначе день строится по файлу
    # и отпечаток у него свой (app/api/ingest_service.py).
    rows = [(r.id, fmt_hhmm(r.window_start), fmt_hhmm(r.window_end), r.address) for r in bundle.requests]
    from_csv = upload(client, "east.csv", csv_bytes(rows))

    weights = workload_weights(DEFAULT_WORKLOAD_LEVEL)
    prints = [
        problem_fingerprint(deps.registry.get(dataset_id).session.problem, weights)
        for dataset_id in (from_bundle, from_csv)
    ]
    assert (
        prints[0] == prints[1] == load_night_plan(night_plan_path(tmp_path / "bundles", REGION)).fingerprint
    )
    assert client.get(f"/api/datasets/{from_csv}").json()["report"]["source"] == "beeline_csv"
    assert client.get(f"/api/datasets/{from_csv}/state").json()["precomputed"] is not None


# --- scripts/night_plan.py ---


def save_region(tmp_path):
    bundle = sample_bundle()
    save_bundle(bundle, tmp_path / "bundles" / REGION / "bundle.json")
    return bundle


# Один регион в одном процессе: поиск идёт на месте, без пула.
ONE_PROCESS = ["--workers", "1", "--parallel", "1"]


def run_cli(tmp_path, *extra, minutes="0.02"):
    """Скрипт на крошечном дне без OSRM. Живой поиск, как у сервиса, — одна стратегия на месте, 1 секунда."""
    argv = ["--region", REGION, "--minutes", minutes, *ONE_PROCESS, "--osrm", "off", *extra]
    if "--out" not in extra:
        # Без OSRM скрипт требует каталог явно: здесь это те же бандлы.
        argv += ["--out", str(tmp_path / "bundles")]
    env = {"DATA_DIR": str(tmp_path), "SOLVER_WORKERS": "1", "SOLVER_TIME_LIMIT_LUNCH_S": "1"}
    return cli.main(argv, env=env)


def test_script_round_trips_on_a_tiny_day(tmp_path, capsys):
    bundle = save_region(tmp_path)

    assert run_cli(tmp_path) == 0

    out = capsys.readouterr().out
    assert "caffeinate -i" in out and "записан:" in out and "поиск 1 с на регион" in out
    # Точка сравнения — поиск, как у сервиса при загрузке, на той же задаче; report.md подписан как другой расчёт.
    assert "живой поиск 1 с, как у сервиса:" in out and "report.md (30 с, без 2ГИС):" in out
    night = load_night_plan(night_plan_path(tmp_path / "bundles", REGION))
    assert (night.region, night.time_limit_s, night.workers, night.workload_level, night.lunch_enabled) == (
        REGION,
        1,
        1,
        DEFAULT_WORKLOAD_LEVEL,
        True,
    )
    assert night.search_s >= 1
    assert night.metrics.assigned == 3 and night.metrics.unassigned == 0
    assert sorted(rid for sequence in night.routes.values() for rid in sequence) == ["R1", "R2", "R3"]
    # Сервис на тех же бандлах строит ту же задачу и берёт ночной план без поиска.
    settings = Settings.from_env({"DATA_DIR": str(tmp_path)})
    ctx = planning_context(settings, None, None, time_limit_s=1, time_limit_lunch_s=1)
    session = start_session("d_night", REGION, OFFICE, bundle.requests, bundle.engineers, None, ctx)
    assert session.precomputed == night.precomputed()
    assert session.precomputed.search_minutes == night.search_s / 60
    assert plan_routes(session.plan) == night.routes

    # Второй запуск стартует от маршрутов прежнего плана, продолжает его поиск и не делает хуже.
    assert run_cli(tmp_path) == 0
    out = capsys.readouterr().out
    assert "маршруты допустимы" in out and "старт от прежнего ночного плана" in out
    again = load_night_plan(night_plan_path(tmp_path / "bundles", REGION))
    assert again.fingerprint == night.fingerprint and again.cost <= night.cost
    assert again.search_s > night.search_s


def test_script_keeps_a_cheaper_night_plan_with_the_same_fingerprint(tmp_path, capsys, monkeypatch):
    bundle = save_region(tmp_path)
    settings = Settings.from_env({"DATA_DIR": str(tmp_path)})
    ctx = planning_context(settings, None, None)
    kept = write_night_plan(tmp_path / "bundles", ctx, bundle.requests, bundle.engineers)
    # Поиск на этот раз «нашёл» план хуже: каждая заявка у своей бригады.
    problem = day_problem(bundle.requests, bundle.engineers, ctx, DEFAULT_WORKLOAD_LEVEL, True)
    worse = build_plan(problem, "ortools", {"E1": ["R1", "R2"], "E2": ["R3"]})
    monkeypatch.setattr(cli, "search_plan", lambda *args, **kwargs: worse)

    assert run_cli(tmp_path) == 0

    assert "оставлен прежний: он дешевле по цели" in capsys.readouterr().out
    assert load_night_plan(night_plan_path(tmp_path / "bundles", REGION)) == kept


def test_script_starts_the_long_search_from_the_live_search_of_the_same_task(tmp_path, capsys, monkeypatch):
    bundle = save_region(tmp_path)
    calls = []
    real = cli.search_plan

    def spy(problem, weights, time_limit_s, pool=None, *, share=1, seed=None):
        plan = real(problem, weights, time_limit_s, pool, share=share, seed=seed)
        calls.append((seed, plan))
        return plan

    monkeypatch.setattr(cli, "search_plan", spy)

    assert run_cli(tmp_path) == 0

    # Сначала поиск, как у сервиса при загрузке, с нуля; долгий поиск стартует от его плана и не хуже его.
    (live_seed, live_plan), (night_seed, _) = calls
    assert live_seed is None and night_seed is live_plan
    assert "старт от плана живого поиска" in capsys.readouterr().out
    ctx = planning_context(Settings.from_env({"DATA_DIR": str(tmp_path)}), None, None)
    problem = day_problem(bundle.requests, bundle.engineers, ctx, DEFAULT_WORKLOAD_LEVEL, True)
    night = load_night_plan(night_plan_path(tmp_path / "bundles", REGION))
    assert night.cost <= plan_cost(problem, live_plan, workload_weights(DEFAULT_WORKLOAD_LEVEL))


def test_script_records_how_long_the_search_really_ran_and_carries_it_over(tmp_path, capsys, monkeypatch):
    bundle = save_region(tmp_path)
    settings = Settings.from_env({"DATA_DIR": str(tmp_path)})
    two_hours = write_night_plan(
        tmp_path / "bundles", planning_context(settings, None, None), bundle.requests, bundle.engineers
    )

    # Поиск кончается сразу, как на уснувшем Mac: возвращает план, от которого стартовал.
    def instant(problem, weights, time_limit_s, pool=None, *, share=1, seed=None):
        return seed or build_plan(problem, "ortools", two_hours.routes)

    monkeypatch.setattr(cli, "search_plan", instant)

    assert run_cli(tmp_path, minutes="1") == 0

    out = capsys.readouterr().out
    assert "поиск шёл 1 с из 1 мин — похоже, Mac засыпал" in out
    # Короткий запуск от двухчасового плана той же задачи продолжает его: пометка остаётся «2 ч», а не «1 с».
    night = load_night_plan(night_plan_path(tmp_path / "bundles", REGION))
    assert (night.search_s, night.time_limit_s) == (7200, 60) and night.computed_at != COMPUTED_AT
    assert "поиск с прежним вместе: 2 ч" in out


def test_script_does_not_write_when_osrm_did_not_give_the_matrix(tmp_path, capsys, monkeypatch):
    bundle = save_region(tmp_path)
    settings = Settings.from_env({"DATA_DIR": str(tmp_path)})
    good = write_night_plan(
        tmp_path / "bundles", planning_context(settings, None, None), bundle.requests, bundle.engineers
    )

    class FlakyOsrm:
        """OSRM отвечает на проверку при старте, а запрос матрицы не доходит: расстояния уходят на прямую."""

        def __init__(self, url):
            self.url = url

        def health(self):
            return True

        def table(self, points):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(cli, "OsrmClient", FlakyOsrm)
    argv = ["--region", REGION, "--minutes", "0.02", *ONE_PROCESS, "--osrm", "http://osrm"]

    assert cli.main(argv, env={"DATA_DIR": str(tmp_path), "SOLVER_WORKERS": "1"}) == 1

    out = capsys.readouterr().out
    assert "матрица haversine" in out and "не посчитан: OSRM не отдал матрицу" in out
    assert load_night_plan(night_plan_path(tmp_path / "bundles", REGION)) == good


def test_script_writes_into_another_directory(tmp_path, capsys):
    save_region(tmp_path)
    out = tmp_path / "night"
    assert run_cli(tmp_path, "--out", str(out)) == 0
    assert load_night_plan(night_plan_path(out, REGION)) is not None
    assert not night_plan_path(tmp_path / "bundles", REGION).exists()
    # Вне data/bundles файлы в образ не попадают: про пересборку образа не напоминаем.
    assert "docker compose up -d --build backend" not in capsys.readouterr().out


def test_script_refuses_an_unknown_region_and_a_silent_osrm(tmp_path, capsys):
    save_region(tmp_path)
    assert cli.main(["--region", "nowhere"], env={"DATA_DIR": str(tmp_path)}) == 2
    assert "Нет бандла региона «nowhere»" in capsys.readouterr().err
    assert (
        cli.main(["--region", REGION, "--osrm", "http://127.0.0.1:9"], env={"DATA_DIR": str(tmp_path)}) == 2
    )
    assert "не отвечает" in capsys.readouterr().err
    # Без OSRM план по прямой не подойдёт сервису из docker-compose: в data/bundles он пишется только по --out.
    assert cli.main(["--region", REGION, "--osrm", "off"], env={"DATA_DIR": str(tmp_path)}) == 2
    assert "Укажите каталог явно: --out" in capsys.readouterr().err
    assert not night_plan_path(tmp_path / "bundles", REGION).exists()


def test_script_defaults_keep_every_process_on_its_own_core():
    # Десять ядер и четыре региона: все регионы сразу, по два процесса — ночь занимает один лимит поиска.
    assert (cli.default_workers(10, 4), cli.default_parallel(10, 2, 4)) == (2, 4)
    # Один регион: четыре процесса, больше стратегий в портфеле нет.
    assert (cli.default_workers(10, 1), cli.default_parallel(10, 4, 1)) == (4, 1)
    assert (cli.default_workers(2, 4), cli.default_parallel(2, 1, 4)) == (1, 2)
    assert cli.time_limit_s(120) == 7200 and cli.time_limit_s(0.001) == 1
    assert [cli.search_text(s) for s in (7200, 7212.4, 7260, 5400, 60, 30, 0.2)] == [
        "2 ч",
        "2 ч",
        "2 ч 1 мин",
        "1 ч 30 мин",
        "1 мин",
        "30 с",
        "1 с",
    ]


def test_script_reads_the_30_second_row_of_the_bundle_report(tmp_path):
    report = tmp_path / "report.md"
    report.write_text(
        "| План | Инженеров | Км | Назначено | Не назначено | Нарушений |\n"
        "|---|---|---|---|---|---|\n"
        "| Базовый (FCFS по ТЗ) | 12 | 359.57 | 49 | 17 | 0 |\n"
        "| Оптимизированный (OR-Tools) | 8 | 269.54 | 66 | 0 | 0 |\n",
        encoding="utf-8",
    )
    assert cli.report_metrics(report) == NightMetrics(
        engineers_used=8, total_km=269.54, assigned=66, unassigned=0
    )
    assert cli.report_metrics(tmp_path / "missing.md") is None


def test_night_plan_file_holds_only_ids_and_numbers(tmp_path):
    night = write_night_plan(tmp_path, context())
    data = json.loads(night_plan_path(tmp_path, REGION).read_text(encoding="utf-8"))
    assert set(data) == {
        "fingerprint",
        "region",
        "time_limit_s",
        "search_s",
        "workers",
        "computed_at",
        "workload_level",
        "lunch_enabled",
        "cost",
        "metrics",
        "routes",
    }
    assert load_night_plan(night_plan_path(tmp_path, REGION)) == night
