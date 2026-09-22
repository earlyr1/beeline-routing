"""Ночной план: утренний план каждого региона, найденный заранее долгим поиском, как ежедневный батч.

Запуск из каталога backend, на ночь — под caffeinate, чтобы Mac не уснул от бездействия посреди поиска (от закрытой
крышки caffeinate -i не спасает):
  caffeinate -i ~/.local/bin/uv run python -m scripts.night_plan --region all --minutes 120
  ~/.local/bin/uv run python -m scripts.night_plan --region east --minutes 1 --out /tmp/night

День региона собирается тем же кодом, что и в сервисе: бандл data/bundles/<регион>/bundle.json, контекст
планирования сервиса (app/api/deps.py, planning_context) с OSRM и матрицами 2ГИС из data/transit,
нагрузка по умолчанию и обед по плану. Поэтому отпечаток задачи в файле совпадает с тем, что посчитает сервис на тех
же бандлах, OSRM и файлах 2ГИС, и сервис берёт ночной план утренним без поиска (app/planning/night.py).

Перед долгим поиском регион ищется так же, как сервис при загрузке дня: те же стратегии (SOLVER_WORKERS) и лимит
(SOLVER_TIME_LIMIT_LUNCH_S, 30 с). Это честная точка сравнения на той же задаче, и долгий поиск стартует от лучшего
из этого плана и прежнего ночного, поэтому записанный план по цели не хуже плана этого живого поиска.

Результат — data/bundles/<регион>/night_plan.json (каталог меняет --out): рядом с бандлом, поэтому ночной план
попадает в образ backend при пересборке. В файле только номера и числа. Прежний файл с тем же отпечатком, который
дешевле по цели, не перезаписывается. Если OSRM не ответил на запрос матрицы и расстояния ушли на прямую, регион
не записывается: такой отпечаток сервис с OSRM не повторит. По той же причине --osrm off работает только с --out.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.api.deps import planning_context
from app.domain.models import Bundle, Metrics
from app.geo.osrm import OsrmClient
from app.ingest.bundle import load_bundle
from app.planning.night import (
    NightMetrics,
    NightPlan,
    NightPlanUnreadable,
    load_night_plan,
    night_plan_path,
    plan_routes,
    problem_fingerprint,
    routes_plan,
    save_night_plan,
)
from app.planning.session import PlanningContext, day_problem, search_plan
from app.planning.workload import DEFAULT_WORKLOAD_LEVEL, workload_weights
from app.settings import Settings
from app.solvers.portfolio import PORTFOLIO, SolverPool, plan_cost
from app.solvers.problem import Problem

ALL_REGIONS = "all"
DEFAULT_MINUTES = 120.0
DEFAULT_OSRM_URL = "http://localhost:5050"
OSRM_OFF = "off"
CAFFEINATE = (
    "Долгий расчёт запускайте под caffeinate, иначе Mac уснёт от бездействия посреди поиска: "
    "caffeinate -i ~/.local/bin/uv run python -m scripts.night_plan --region all --minutes 120. "
    "От закрытой крышки caffeinate -i не спасает: на ночь подключите питание и не закрывайте крышку"
)
REBUILD = (
    "Ночные планы лежат рядом с бандлами и копируются в образ backend: пересоберите его — "
    "docker compose up -d --build backend"
)
# Строка оптимизированного плана в report.md бандла: её посчитал prepare поиском с лимитом по умолчанию (30 с),
# с OSRM, но без матриц 2ГИС, то есть на другой задаче: матрицы лежат в репозитории, и у сервиса они есть всегда.
REPORT_ROW = "Оптимизированный"
# Поиск, который шёл заметно меньше лимита: OR-Tools считает лимит по настенным часам, а монотонные часы
# в сне Mac стоят. Так видно, что Mac засыпал посреди поиска.
SLEPT_SHARE = 0.9
# Ширина подписи в строках сводки по региону.
LABEL_WIDTH = 32


class RegionsNotFound(LookupError):
    """Считать нечего: такого региона нет в data/bundles или бандлов нет вовсе."""


def known_regions(bundles_dir: Path) -> list[str]:
    return sorted(path.parent.name for path in Path(bundles_dir).glob("*/bundle.json"))


def pick_regions(bundles_dir: Path, region: str) -> list[str]:
    known = known_regions(bundles_dir)
    if region == ALL_REGIONS:
        if not known:
            raise RegionsNotFound(f"В {bundles_dir} нет ни одного бандла региона.")
        return known
    if region not in known:
        raise RegionsNotFound(
            f"Нет бандла региона «{region}»: {Path(bundles_dir) / region / 'bundle.json'}. "
            f"Готовые регионы: {', '.join(known) or 'ни одного'}"
        )
    return [region]


def default_workers(cpu: int, regions: int) -> int:
    """Процессов на регион: все регионы сразу, если хватает ядер, но не больше стратегий портфеля."""
    return max(1, min(len(PORTFOLIO), cpu // max(1, regions)))


def default_parallel(cpu: int, workers: int, regions: int) -> int:
    """Регионов одновременно: столько, чтобы процессов было не больше ядер."""
    return max(1, min(regions, cpu // workers))


def time_limit_s(minutes: float) -> int:
    """Лимит поиска OR-Tools в целых секундах, не меньше секунды."""
    return max(1, round(minutes * 60))


def search_text(seconds: float) -> str:
    """Сколько шёл поиск или какой у него лимит: «2 ч», «1 ч 30 мин», «12 мин», «30 с» — как пометка в интерфейсе."""
    if seconds < 60:
        return f"{max(1, round(seconds))} с"
    hours, rest = divmod(round(seconds / 60), 60)
    if hours == 0:
        return f"{rest} мин"
    return f"{hours} ч" if rest == 0 else f"{hours} ч {rest} мин"


def row(label: str, text: str) -> str:
    """Строка сводки по региону: подписи выровнены, чтобы цифры планов стояли столбцом."""
    return f"  {label + ':':<{LABEL_WIDTH}} {text}"


def metrics_text(metrics: Metrics | NightMetrics) -> str:
    return (
        f"{metrics.engineers_used} бригад, {metrics.total_km:.2f} км, "
        f"назначено {metrics.assigned}, без исполнителя {metrics.unassigned}"
    )


def night_metrics(metrics: Metrics) -> NightMetrics:
    return NightMetrics(
        engineers_used=metrics.engineers_used,
        total_km=metrics.total_km,
        assigned=metrics.assigned,
        unassigned=metrics.unassigned,
    )


def report_metrics(path: Path) -> NightMetrics | None:
    """Оптимизированный план из report.md бандла (поиск 30 с при сборке бандла, без 2ГИС) или None, если строки нет."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[0].startswith(REPORT_ROW):
            try:
                return NightMetrics(
                    engineers_used=int(cells[1]),
                    total_km=float(cells[2]),
                    assigned=int(cells[3]),
                    unassigned=int(cells[4]),
                )
            except ValueError:
                return None
    return None


def transit_text(problem: Problem) -> str:
    lookup = problem.travel.transit
    if lookup is None:
        return "2ГИС: минут нет, общественный транспорт по формуле"
    points = len(problem.engineers) + len(problem.requests)
    return f"2ГИС: привязалось {lookup.snapped} из {points} точек"


@dataclass
class LiveSearch:
    """Поиск при загрузке дня, как у сервиса: пул SOLVER_WORKERS процессов (None — один поиск на месте).

    Регионы ищут его по очереди под lock: так он не делит ядра с другими живыми поисками и повторяет сервис.
    """

    pool: SolverPool | None
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class RegionResult:
    region: str
    lines: list[str] = field(default_factory=list)
    written: bool = False
    failed: bool = False
    seconds: float = 0.0


def run_region(
    region: str,
    bundles_dir: Path,
    out_dir: Path,
    ctx: PlanningContext,
    limit_s: int,
    workers: int,
    in_process: bool,
    live: LiveSearch,
) -> RegionResult:
    """Ищет ночной план одного региона и пишет файл, если он не хуже прежнего с тем же отпечатком.

    Сначала — поиск, как у сервиса при загрузке дня, на той же задаче; долгий поиск стартует от лучшего из его плана
    и прежнего ночного.
    """
    started = time.monotonic()
    result = RegionResult(region)
    bundle: Bundle = load_bundle(bundles_dir / region / "bundle.json")
    weights = workload_weights(DEFAULT_WORKLOAD_LEVEL)
    problem = day_problem(bundle.requests, bundle.engineers, ctx, DEFAULT_WORKLOAD_LEVEL, True)
    fingerprint = problem_fingerprint(problem, weights)
    result.lines.append(
        f"{bundle.office.title} ({region}): матрица {problem.travel.base.source}, {transit_text(problem)}, "
        f"отпечаток {fingerprint[:16]}…"
    )
    if ctx.osrm is not None and problem.travel.base.source != "osrm":
        # OSRM ответил на проверку при старте, но не на запрос матрицы: расстояния ушли на прямую. Отпечаток такой
        # задачи сервис с OSRM не повторит, а хороший прежний план был бы заменён бесполезным.
        result.failed = True
        result.lines.append(
            "  не посчитан: OSRM не отдал матрицу, расстояния по прямой; прежний файл не тронут"
        )
        return result

    path = night_plan_path(out_dir, region)
    previous_note = "нет"
    previous = None
    try:
        previous = load_night_plan(path)
    except NightPlanUnreadable as error:
        previous_note = f"файл не читается ({error}), будет перезаписан"
    previous_plan = None
    same_task = previous is not None and previous.fingerprint == fingerprint
    if previous is not None:
        previous_plan, reason = routes_plan(problem, previous.routes)
        same = "отпечаток тот же" if same_task else "отпечаток другой"
        valid = "маршруты допустимы" if previous_plan else f"маршруты недопустимы: {reason}"
        previous_note = (
            f"{metrics_text(previous.metrics)} (поиск {search_text(previous.search_s)}, "
            f"посчитан {previous.computed_at}; {same}, {valid})"
        )

    # Поиск, как у сервиса при загрузке дня, на той же задаче: с ним и сравнивать долгий поиск.
    live_limit_s = ctx.day_time_limit_s(True)
    with live.lock:
        live_plan = search_plan(problem, weights, live_limit_s, live.pool)
    live_cost = plan_cost(problem, live_plan, weights)
    # Долгий поиск стартует от лучшего из прежнего ночного плана и живого поиска; при равной цели — от прежнего,
    # чтобы его поиск продолжился, а не начался заново.
    seed = live_plan
    if previous_plan is not None and plan_cost(problem, previous_plan, weights) <= live_cost:
        seed = previous_plan

    pool = None if in_process else SolverPool(workers)
    searches = 1 if pool is None else len(pool.strategies())
    search_started = time.monotonic()
    try:
        plan = search_plan(problem, weights, limit_s, pool, seed=seed)
    finally:
        # Монотонные часы: сколько поиск шёл на самом деле. Во сне Mac они стоят, а лимит OR-Tools идёт.
        search_s = time.monotonic() - search_started
        if pool is not None:
            pool.shutdown()
    cost = plan_cost(problem, plan, weights)
    # Поиск от прежнего плана той же задачи продолжает его: в файл идёт общее время обоих.
    continued = same_task and seed is previous_plan
    total_s = search_s + (previous.search_s if continued and previous is not None else 0)
    result.seconds = time.monotonic() - started

    start_note = "прежнего ночного плана" if seed is previous_plan else "плана живого поиска"
    result.lines.append(
        row(
            "новый ночной план",
            f"{metrics_text(plan.metrics)} (поиск {search_text(search_s)} при лимите {search_text(limit_s)}, "
            f"поисков одновременно {searches}, старт от {start_note})",
        )
    )
    result.lines.append(
        row(f"живой поиск {search_text(live_limit_s)}, как у сервиса", metrics_text(live_plan.metrics))
    )
    result.lines.append(row("прежний ночной план", previous_note))
    report = report_metrics(bundles_dir / region / "report.md")
    report_text = metrics_text(report) if report else "строки нет"
    if report and problem.travel.transit is not None:
        report_text += " — другая задача: общественный транспорт там по формуле, а не по 2ГИС"
    result.lines.append(row("report.md (30 с, без 2ГИС)", report_text))
    control = bundle.control_plan.metrics if bundle.control_plan is not None else None
    result.lines.append(row("диспетчеры", metrics_text(control) if control else "плана нет"))
    if search_s < SLEPT_SHARE * limit_s:
        result.lines.append(
            f"  внимание: поиск шёл {search_text(search_s)} из {search_text(limit_s)} — похоже, Mac засыпал; "
            "в файл записано, сколько он шёл на самом деле"
        )

    if plan.violations:
        # Солвер чинит маршруты до допустимых, так что это ошибка кода: такой план сервису отдавать нельзя.
        result.failed = True
        result.lines.append(f"  не записан: в плане нарушения ({plan.violations[0]})")
        return result
    if same_task and previous_plan is not None and plan_cost(problem, previous_plan, weights) < cost:
        result.lines.append(f"  оставлен прежний: он дешевле по цели — {path}")
        return result
    save_night_plan(
        NightPlan(
            fingerprint=fingerprint,
            region=region,
            time_limit_s=limit_s,
            search_s=max(1, round(total_s)),
            workers=searches,
            computed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            workload_level=DEFAULT_WORKLOAD_LEVEL,
            lunch_enabled=True,
            cost=cost,
            metrics=night_metrics(plan.metrics),
            routes=plan_routes(plan),
        ),
        path,
    )
    result.written = True
    if previous is not None and not same_task:
        result.lines.append(
            "  внимание: прежний ночной план посчитан на другой задаче и заменён — так и должно быть после "
            "пересборки бандлов или матриц 2ГИС; иначе проверьте, что OSRM и 2ГИС те же, что у сервиса"
        )
    if continued:
        result.lines.append(f"  поиск с прежним вместе: {search_text(total_s)}")
    result.lines.append(f"  записан: {path}")
    return result


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ищет утренний план каждого региона заранее долгим поиском и кладёт его рядом с бандлом"
    )
    parser.add_argument(
        "--region", required=True, help=f"регион из data/bundles, например east, или {ALL_REGIONS} — все"
    )
    parser.add_argument(
        "--minutes",
        type=float,
        default=DEFAULT_MINUTES,
        help=f"лимит поиска на регион, минуты (по умолчанию {DEFAULT_MINUTES:g})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        help=f"процессов поиска на регион, полезно не больше {len(PORTFOLIO)} (по умолчанию по числу ядер)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        help="регионов одновременно (по умолчанию так, чтобы процессов было не больше ядер)",
    )
    parser.add_argument("--out", type=Path, help="каталог ночных планов (по умолчанию data/bundles)")
    parser.add_argument(
        "--osrm",
        help=(
            f"адрес OSRM (по умолчанию OSRM_URL или {DEFAULT_OSRM_URL}); {OSRM_OFF} — расстояния по прямой, "
            "только с --out"
        ),
    )
    return parser.parse_args(argv)


def _osrm(args: argparse.Namespace, env: Mapping[str, str]) -> tuple[OsrmClient | None, str]:
    url = args.osrm or (env.get("OSRM_URL") or "").strip() or DEFAULT_OSRM_URL
    if url == OSRM_OFF:
        return None, "OSRM выключен: расстояния по прямой, как у сервиса без OSRM_URL"
    return OsrmClient(url), f"OSRM {url}"


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    args = _parse_args(argv)
    settings = Settings.from_env(env)
    if args.minutes <= 0:
        print("Лимит поиска --minutes должен быть больше нуля", file=sys.stderr)
        return 2
    try:
        regions = pick_regions(settings.bundles_dir, args.region)
    except RegionsNotFound as error:
        print(str(error), file=sys.stderr)
        return 2
    cpu = os.cpu_count() or 1
    workers = args.workers or default_workers(cpu, len(regions))
    parallel = args.parallel or default_parallel(cpu, workers, len(regions))
    if workers < 1 or parallel < 1:
        print("--workers и --parallel должны быть не меньше 1", file=sys.stderr)
        return 2
    osrm, osrm_note = _osrm(args, env)
    if osrm is not None and not osrm.health():
        print(
            f"{osrm_note} не отвечает. Без него матрица не та, что у сервиса, и ночной план не подойдёт. "
            "Поднимите OSRM: docker compose up -d osrm",
            file=sys.stderr,
        )
        return 2
    if osrm is None and args.out is None:
        # Сервис в docker-compose всегда считает с OSRM: план по прямой ему не подойдёт, а прежний хороший
        # план в data/bundles был бы заменён.
        print(
            f"{osrm_note}. Такой ночной план подойдёт только сервису без OSRM, а прежние планы в "
            f"{settings.bundles_dir} были бы заменены. Укажите каталог явно: --out",
            file=sys.stderr,
        )
        return 2
    out_dir = args.out or settings.bundles_dir
    limit_s = time_limit_s(args.minutes)
    # Поиск, как у сервиса при загрузке дня: тот же лимит и столько же стратегий, по региону за раз.
    live_limit_s = settings.solver_time_limit_lunch_s
    live_workers = settings.solver_workers

    print(CAFFEINATE)
    rounds = math.ceil(len(regions) / parallel)
    finish = datetime.now() + timedelta(seconds=rounds * limit_s + len(regions) * live_limit_s)
    print(
        f"регионов: {len(regions)} ({', '.join(regions)}), одновременно: {parallel}, процессов на регион: "
        f"{workers}, поиск {search_text(limit_s)} на регион; перед ним поиск, как у сервиса при загрузке "
        f"({search_text(live_limit_s)}, стратегий {min(live_workers, len(PORTFOLIO))}), по региону за раз; "
        f"закончится около {finish:%H:%M}"
    )
    if workers > len(PORTFOLIO):
        print(f"больше {len(PORTFOLIO)} процессов на регион не нужно: в портфеле {len(PORTFOLIO)} стратегии")
    if workers * parallel > cpu:
        print(f"процессов {workers * parallel} больше, чем ядер ({cpu}): поиски будут мешать друг другу")
    print(f"{osrm_note}; каталог ночных планов: {out_dir}", flush=True)

    # Без кэша OSRM: ночной расчёт берёт матрицу у того OSRM, что работает сейчас, а не из старого кэша.
    ctx = planning_context(settings, osrm, None)
    # Поиск одного региона в одном процессе идёт прямо здесь: так быстрее на маленьких задачах и в тестах.
    in_process = workers == 1 and parallel == 1
    # Пул живого поиска — как у сервиса (app/api/deps.py, build_deps): при SOLVER_WORKERS 1 поиск идёт на месте.
    live = LiveSearch(SolverPool(live_workers) if live_workers > 1 else None)
    printing = threading.Lock()

    def job(region: str) -> RegionResult:
        try:
            result = run_region(
                region, settings.bundles_dir, out_dir, ctx, limit_s, workers, in_process, live
            )
        except Exception as error:  # noqa: BLE001 - сбой одного региона не должен стоить ночи остальным
            result = RegionResult(region, [f"{region}: ночной план не посчитан — {error!r}"], failed=True)
        with printing:
            print("\n".join(result.lines), flush=True)
        return result

    try:
        with ThreadPoolExecutor(max_workers=parallel) as executor:
            results: Sequence[RegionResult] = list(executor.map(job, regions))
    finally:
        if live.pool is not None:
            live.pool.shutdown()

    written = sum(result.written for result in results)
    failed = sum(result.failed for result in results)
    print(
        f"записано ночных планов: {written}, оставлено прежних: {len(results) - written - failed}, с ошибкой: {failed}"
    )
    if written and out_dir == settings.bundles_dir:
        print(REBUILD)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
