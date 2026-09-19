"""CLI: сырые CSV региона -> data/bundles/<region>/bundle.json + report.md.

Запуск из каталога backend:  uv run python -m app.synth.prepare --region all
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.domain.models import Bundle, Metrics, Office, Plan
from app.geo.kvcache import KVCache
from app.geo.matrix import TrafficProfile, TravelModel
from app.geo.osrm import OsrmClient
from app.ingest.beeline_csv import RawFile, parse_beeline_csv
from app.ingest.bundle import save_bundle
from app.ingest.geocode import Geocoder, JsonGeocodeCache, NominatimGeocoder, geocode_address
from app.planning.workload import DEFAULT_WORKLOAD_LEVEL, travel_buffer, workload_weights
from app.settings import DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S, MAX_DEFAULT_SOLVER_WORKERS
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import OrToolsSolver
from app.solvers.portfolio import SolverPool
from app.solvers.problem import make_problem
from app.synth.config import SynthConfig
from app.synth.control import build_control_plan
from app.synth.engineers import build_engineers
from app.synth.events import build_demo_events
from app.synth.requests import build_requests, check_alignment

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


@dataclass
class PrepareResult:
    bundle: Bundle
    fcfs: Plan
    optimized: Plan
    report: str
    self_check_ok: bool


def lexicographic(metrics: Metrics) -> tuple[int, int, float]:
    return metrics.unassigned, metrics.engineers_used, round(metrics.total_km, 2)


def self_check(fcfs: Metrics, optimized: Metrics) -> tuple[bool, str]:
    base, best = lexicographic(fcfs), lexicographic(optimized)
    if best > base:
        return False, f"Оптимизированный план хуже базового: {best} против {base}"
    if best == base:
        return False, "Базовый вариант не уступает оптимизированному: в данных нет конфликта из ТЗ"
    return True, "Базовый вариант уступает оптимизированному хотя бы по одной обязательной метрике"


def render_report(
    title: str,
    region: str,
    synthetic: RawFile,
    bundle: Bundle,
    source: str,
    plans: list[tuple[str, Plan]],
    check: tuple[bool, str],
) -> str:
    precision = Counter(request.geocode_precision for request in bundle.requests)
    lines = [
        f"# Регион {title} ({region})",
        "",
        "## Данные",
        "",
        "| Показатель | Значение |",
        "|---|---|",
        f"| Заявок | {len(bundle.requests)} |",
        f"| Инженеров | {len(bundle.engineers)} |",
        f"| Отброшено строк | {len(synthetic.skipped)} |",
        f"| Источник матрицы | {source} |",
        (
            f"| Геокодирование: дом / улица / район / не найдено | {precision['house']} / "
            f"{precision['street']} / {precision['locality']} / {precision['none']} |"
        ),
        "",
        "## Сравнение планов",
        "",
        "| План | Инженеров | Км | Назначено | Не назначено | Нарушений |",
        "|---|---|---|---|---|---|",
    ]
    for label, plan in plans:
        m = plan.metrics
        lines.append(
            f"| {label} | {m.engineers_used} | {m.total_km} | {m.assigned} | {m.unassigned} | {m.violations} |"
        )
    lines += ["", "## Самопроверка", "", ("OK: " if check[0] else "FAIL: ") + check[1]]
    control = next((plan for label, plan in plans if plan.solver == "dispatchers"), None)
    if control is not None and control.violations:
        lines += ["", "## Нарушения в плане диспетчеров (первые 15)", ""]
        lines += [f"- {violation}" for violation in control.violations[:15]]
    if synthetic.skipped:
        lines += ["", "## Отброшенные строки", ""] + [f"- {item}" for item in synthetic.skipped]
    missing = [r for r in bundle.requests if r.geocode_precision == "none"]
    if missing:
        lines += ["", "## Адреса не найдены", ""] + [f"- {r.id}: {r.address}" for r in missing]
    return "\n".join(lines) + "\n"


def prepare_region(
    region: str,
    cfg: SynthConfig,
    *,
    repo_root: Path,
    geocoder: Geocoder | None,
    osrm: OsrmClient | None,
    cache: KVCache | None,
    time_limit_s: int,
    traffic: TrafficProfile,
    pool: SolverPool | None = None,
) -> PrepareResult:
    region_cfg = cfg.regions[region]
    control = parse_beeline_csv((repo_root / region_cfg.control).read_bytes())
    synthetic = parse_beeline_csv((repo_root / region_cfg.synthetic).read_bytes())
    check_alignment(synthetic, control)
    if not synthetic.office_address:
        raise ValueError(f"В файле {region_cfg.synthetic} нет строки «Адрес офиса»")

    geo_cache = JsonGeocodeCache(repo_root / "data" / "geocode_cache.json")
    try:
        office_geo = geocode_address(synthetic.office_address, "", geocoder, geo_cache)
        if office_geo.lat is None or office_geo.lon is None:
            raise ValueError(f"Не удалось геокодировать адрес офиса: {synthetic.office_address}")
        office = Office(
            region=region,
            title=region_cfg.title,
            address=synthetic.office_address,
            lat=office_geo.lat,
            lon=office_geo.lon,
        )
        requests = build_requests(
            cfg,
            synthetic,
            control,
            lambda address, district: geocode_address(address, district, geocoder, geo_cache),
        )
    finally:
        geo_cache.save()

    row_points = {
        k: (r.lat, r.lon) for k, r in enumerate(requests) if r.lat is not None and r.lon is not None
    }
    engineers, crew_to_engineer = build_engineers(cfg, region, control, office, row_points)
    # Бандл считается как день сервиса по умолчанию: уровень нагрузки по умолчанию и обед по плану, лимит OR-Tools
    # по умолчанию как у дня с обедом. Отчёт совпадает с тем, что сервис покажет без выбора уровня и обеда.
    problem = make_problem(
        requests,
        engineers,
        model=TravelModel(),
        traffic=traffic,
        osrm=osrm,
        cache=cache,
        buffer=travel_buffer(DEFAULT_WORKLOAD_LEVEL),
        lunch=True,
    )
    fcfs = FcfsSolver().solve(problem)
    weights = workload_weights(DEFAULT_WORKLOAD_LEVEL)
    # Бандл считается тем же портфелем стратегий, что и сервис при SOLVER_WORKERS > 1: иначе таблица
    # результатов показывала бы план хуже того, который диспетчер увидит на экране.
    if pool is not None:
        optimized = pool.solve(problem, weights, time_limit_s, pool.strategies())
    else:
        optimized = OrToolsSolver(time_limit_s=time_limit_s, weights=weights).solve(problem)
    control_plan = build_control_plan(problem, control, synthetic, crew_to_engineer)
    events = build_demo_events(cfg, region, requests, control, synthetic, crew_to_engineer, optimized)
    bundle = Bundle(
        region=region,
        office=office,
        requests=requests,
        engineers=engineers,
        events=events,
        control_plan=control_plan,
    )
    check = self_check(fcfs.metrics, optimized.metrics)
    report = render_report(
        region_cfg.title,
        region,
        synthetic,
        bundle,
        problem.travel.base.source,
        [
            ("Базовый (FCFS по ТЗ)", fcfs),
            ("Оптимизированный (OR-Tools)", optimized),
            ("Диспетчеры (контрольное распределение)", control_plan),
        ],
        check,
    )
    return PrepareResult(bundle=bundle, fcfs=fcfs, optimized=optimized, report=report, self_check_ok=check[0])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Готовит бандлы данных по регионам из выгрузки Билайна")
    parser.add_argument("--region", default="all", help="east | south_east | south_center | north_west | all")
    parser.add_argument(
        "--osrm-url", default=os.environ.get("OSRM_URL"), help="например http://localhost:5000"
    )
    parser.add_argument("--geocoder", choices=["nominatim", "cache-only"], default="nominatim")
    parser.add_argument(
        "--time-limit", type=int, default=DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S, help="секунд на OR-Tools"
    )
    parser.add_argument(
        "--solver-workers",
        type=int,
        default=MAX_DEFAULT_SOLVER_WORKERS,
        help="процессов поиска OR-Tools: несколько стратегий за тот же лимит; 1 — одна стратегия без пула",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = SynthConfig.load(BACKEND_DIR / "config" / "synth_config.yaml")
    regions = list(cfg.regions) if args.region == "all" else [args.region]
    unknown = [region for region in regions if region not in cfg.regions]
    if unknown:
        parser.error(f"неизвестный регион: {', '.join(unknown)}")

    geocoder = NominatimGeocoder() if args.geocoder == "nominatim" else None
    osrm = OsrmClient(args.osrm_url) if args.osrm_url else None
    cache = KVCache(REPO_ROOT / "data" / "cache.sqlite")
    traffic = TrafficProfile.load(BACKEND_DIR / "config" / "traffic_profile.yaml")

    pool = SolverPool(args.solver_workers) if args.solver_workers > 1 else None
    all_ok = True
    try:
        for region in regions:
            result = prepare_region(
                region,
                cfg,
                repo_root=REPO_ROOT,
                geocoder=geocoder,
                osrm=osrm,
                cache=cache,
                time_limit_s=args.time_limit,
                traffic=traffic,
                pool=pool,
            )
            out_dir = REPO_ROOT / "data" / "bundles" / region
            save_bundle(result.bundle, out_dir / "bundle.json")
            (out_dir / "report.md").write_text(result.report, encoding="utf-8")
            print(result.report)
            all_ok = all_ok and result.self_check_ok
    finally:
        if pool is not None:
            pool.shutdown()
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
