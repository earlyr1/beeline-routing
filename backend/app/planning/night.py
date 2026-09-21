"""Ночной план: утренний план дня, посчитанный заранее долгим поиском (scripts/night_plan.py).

Поиск OR-Tools при загрузке дня идёт до лимита (30 секунд с обедом) и нередко останавливается в локальном оптимуме:
на Востоке с матрицами 2ГИС экран показывал 9 бригад, хотя допустимый план на 8 бригад есть. В промышленной версии
это ежедневный батч: ночью план каждого региона ищется столько, сколько не жалко, а утром сервис берёт готовый план,
и события дня пересчитываются от почти оптимального плана за секунды.

Файл лежит рядом с бандлом, data/bundles/<регион>/night_plan.json, и попадает в образ backend вместе с ним. В файле
только номера и числа: отпечаток задачи, маршруты номерами заявок по бригадам и итоги. Минут 2ГИС в нём нет,
отпечаток — хэш SHA-256, из которого их не восстановить.

Сервис берёт ночной план, только если отпечаток задачи дня совпал с отпечатком из файла, а маршруты файла на задаче
дня проходят проверку всех ограничений (build_plan и simulate_route) и назначают столько же заявок. Иначе план
ищется при загрузке, как раньше; если маршруты файла на задаче дня всё ещё допустимы, поиск стартует от них и
не может дать план хуже ночного по цели при текущих весах.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.domain.models import Engineer, Plan, Request
from app.planning.models import PrecomputedPlan
from app.solvers.assemble import build_plan
from app.solvers.eligibility import exclusion
from app.solvers.ortools_solver import ObjectiveWeights, OrToolsSolver
from app.solvers.problem import Problem

logger = logging.getLogger(__name__)

NIGHT_PLAN_FILE = "night_plan.json"
# Версия способа считать отпечаток: при его изменении старые файлы перестают совпадать, а не совпадают по ошибке.
FINGERPRINT_VERSION = 1
# Километры в отпечатке — с точностью до миллиметра: так хэш не зависит от последнего бита тригонометрии
# (гаверсинус на macOS и в Linux-контейнере может разойтись в 17-м знаке).
KM_DIGITS = 6


class NightMetrics(BaseModel):
    engineers_used: int
    total_km: float
    assigned: int
    unassigned: int


class NightPlan(BaseModel):
    """Содержимое night_plan.json. Только номера и числа: данных 2ГИС в файле нет."""

    fingerprint: str
    region: str
    time_limit_s: int = Field(gt=0)  # лимит поиска последнего запуска скрипта
    # Сколько шёл поиск, который нашёл план: секунды по монотонным часам, а не лимит. Уснувший Mac делает поиск
    # короче лимита. Запуск, стартовавший от прежнего плана той же задачи, прибавляет его секунды.
    search_s: int = Field(gt=0)
    workers: int = Field(ge=1)  # сколько процессов искали одновременно
    computed_at: str  # когда поиск закончился, ISO 8601 с часовым поясом
    workload_level: int
    lunch_enabled: bool
    cost: int  # стоимость по цели OR-Tools при весах уровня нагрузки (app/solvers/portfolio.py, plan_cost)
    metrics: NightMetrics
    # Маршруты: номера заявок по порядку у каждой бригады с визитами.
    routes: dict[str, list[str]]

    def precomputed(self) -> PrecomputedPlan:
        return PrecomputedPlan(search_minutes=self.search_s / 60, computed_at=self.computed_at)


def night_plan_path(directory: Path, region: str) -> Path:
    """Файл ночного плана региона: рядом с его бандлом."""
    return Path(directory) / region / NIGHT_PLAN_FILE


def plain_region(region: str) -> bool:
    """Регион — имя одного каталога: так загруженный бандл с регионом «/etc/x» или «..» не уводит поиск файла."""
    return region not in ("", ".", "..") and "\x00" not in region and Path(region).name == region


def save_night_plan(plan: NightPlan, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")


class NightPlanUnreadable(ValueError):
    """Файл ночного плана есть, но прочитать его нельзя: текст — для лога."""


def load_night_plan(path: Path) -> NightPlan | None:
    """Ночной план из файла или None, если файла нет. Битый файл — NightPlanUnreadable."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    # ValueError — файл не в UTF-8 (UnicodeDecodeError) или нулевой байт в пути.
    except (OSError, ValueError) as error:
        raise NightPlanUnreadable(str(error)) from error
    try:
        return NightPlan.model_validate_json(text)
    except ValidationError as error:
        first = error.errors()[0]
        raise NightPlanUnreadable(
            f"{'.'.join(str(part) for part in first['loc'])}: {first['msg']}"
        ) from error


def plan_routes(plan: Plan) -> dict[str, list[str]]:
    """Маршруты плана номерами заявок: только бригады с визитами, в порядке бригад плана."""
    return {
        route.engineer_id: [visit.request_id for visit in route.visits]
        for route in plan.routes
        if route.visits
    }


def _request_key(request: Request) -> list:
    """Всё, что о заявке читают солверы и прогон маршрута. Адрес, район и типы BK/HD в решение не входят."""
    return [
        request.id,
        None if request.lat is None else round(request.lat, 6),
        None if request.lon is None else round(request.lon, 6),
        request.duration_min,
        request.window_start,
        request.window_end,
        request.priority.value,
        request.tier.value,
        request.asap,
        request.skill.value,
        None if request.transport_required is None else request.transport_required.value,
        request.status.value,
        request.needs_equipment,
        request.fixed_engineer_id,
    ]


def _engineer_key(engineer: Engineer) -> list:
    """Всё, что об инженере читают солверы и прогон маршрута. Имя бригады идёт только в тексты."""
    return [
        engineer.id,
        round(engineer.start_lat, 6),
        round(engineer.start_lon, 6),
        engineer.shift_start,
        engineer.shift_end,
        sorted(skill.value for skill in engineer.skills),
        engineer.transport.value,
        engineer.available,
        engineer.unavailable_from,
        engineer.equipment_stock,
    ]


def problem_fingerprint(problem: Problem, weights: ObjectiveWeights) -> str:
    """Отпечаток задачи начала дня: SHA-256 всего, что определяет оптимизацию, как её строит сервис.

    Входят заявки и инженеры (поля, которые читают солверы и прогон маршрута), обед, веса цели и запас на дорогу
    уровня нагрузки, а также минуты и километры, которыми солверы реально пользуются: из каждой точки дня в каждую
    заявку для каждого транспорта бригад дня, с пробками, запасом и минутами 2ГИС, как их отдаёт Problem.travel_min.
    Поэтому другой OSRM, другие файлы 2ГИС или другая модель дороги дают другой отпечаток. Порядок заявок и
    инженеров во входных данных на отпечаток не влияет: точки идут по номерам. Отпечаток одинаков в разных процессах
    и на разных машинах: встроенный hash() и порядок множеств в нём не участвуют, километры округлены до
    миллиметра. Отпечаток считается для задачи начала дня: закреплённых визитов в ней нет.
    """
    requests = sorted(problem.requests, key=lambda request: request.id)
    engineers = sorted(problem.engineers, key=lambda engineer: engineer.id)
    states = {state.engineer.id: state for state in problem.states}
    transports = sorted({engineer.transport for engineer in engineers})
    header = {
        "version": FINGERPRINT_VERSION,
        "lunch": problem.lunch,
        "now": problem.now,
        "buffer": [problem.buffer.factor, problem.buffer.min_extra],
        "weights": asdict(weights),
        "requests": [_request_key(request) for request in requests],
        "open": sorted(problem.open_request_ids),
        "unplannable": sorted(item.request_id for item in problem.unplannable),
        "engineers": [_engineer_key(engineer) for engineer in engineers],
        "states": [
            [
                engineer.id,
                states[engineer.id].available_from,
                states[engineer.id].available_until,
                states[engineer.id].equipment_left,
            ]
            for engineer in engineers
        ],
        "leg_limits": {
            transport.value: problem.travel.model.leg_limit_km(transport) for transport in transports
        },
    }
    digest = hashlib.sha256()
    digest.update(json.dumps(header, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
    # Узлы задачи по номерам, а не по месту во входных данных. В стартовые точки бригад никто не въезжает,
    # поэтому назначения — только заявки.
    sources = sorted(
        [(f"e:{engineer.id}", k) for k, engineer in enumerate(problem.engineers)]
        + [(f"r:{request.id}", problem.request_node(request.id)) for request in problem.requests]
    )
    targets = [node for label, node in sources if label.startswith("r:")]
    for transport in transports:
        # Время и километры зависят только от транспорта бригады: берётся первая по номеру бригада с ним.
        engineer = next(engineer for engineer in engineers if engineer.transport == transport)
        digest.update(f"|{transport.value}|".encode())
        for _, a in sources:
            row = ",".join(
                f"{problem.travel_min(a, b, engineer)}:{problem.travel_km(a, b, engineer):.{KM_DIGITS}f}"
                for b in targets
            )
            digest.update(row.encode())
            digest.update(b";")
    return digest.hexdigest()


def routes_plan(problem: Problem, routes: Mapping[str, Sequence[str]]) -> tuple[Plan | None, str]:
    """План из маршрутов ночного файла на задаче дня или None и причина, почему маршруты не годятся.

    Годятся маршруты, в которых каждая бригада есть в дне, каждая заявка открыта и стоит в маршрутах один раз,
    бригада может взять заявку (закрепление, навык, транспорт, доступность) и прогон маршрута не находит ни одного
    нарушения: окна, смены, обед, оборудование, предел плеча.
    """
    open_ids = set(problem.open_request_ids)
    states = {state.engineer.id: state for state in problem.states}
    seen: set[str] = set()
    for engineer_id, sequence in routes.items():
        state = states.get(engineer_id)
        if state is None:
            return None, f"бригады {engineer_id} нет в дне"
        for request_id in sequence:
            if request_id not in open_ids:
                return None, f"заявки {request_id} нет среди открытых заявок дня"
            if request_id in seen:
                return None, f"заявка {request_id} стоит в маршрутах дважды"
            seen.add(request_id)
            if exclusion(problem.request(request_id), state) is not None:
                return None, f"бригада {engineer_id} не может взять заявку {request_id}"
    plan = build_plan(problem, OrToolsSolver.name, {key: list(value) for key, value in routes.items()})
    if plan.violations:
        return None, f"нарушений: {len(plan.violations)}, первое — {plan.violations[0]}"
    return plan, ""


@dataclass(frozen=True)
class NightChoice:
    """Что делать с ночным планом при сборке дня."""

    # Ночной план подошёл: это утренний план, поиск не нужен.
    plan: Plan | None = None
    precomputed: PrecomputedPlan | None = None
    # Ночной план не подошёл, но его маршруты допустимы на задаче дня: поиск стартует от них.
    seed: Plan | None = None


def choose_night_plan(
    directory: Path | None, region: str, problem: Problem, weights: ObjectiveWeights
) -> NightChoice:
    """Решает, брать ли ночной план региона утренним планом, и пишет в лог одну строку, почему да или нет.

    directory — каталог ночных планов (<каталог>/<регион>/night_plan.json); None — ночные планы не подключены.
    """
    if directory is None:
        return NightChoice()
    if not plain_region(region):
        logger.warning("Ночной план региона %r не ищется: регион не имя каталога", region)
        return NightChoice()
    path = night_plan_path(directory, region)
    try:
        night = load_night_plan(path)
    except NightPlanUnreadable as error:
        logger.warning("Ночной план региона %s не подошёл: файл %s не читается (%s)", region, path, error)
        return NightChoice()
    if night is None:
        logger.info("Ночной план региона %s не подошёл: файла %s нет, план ищется при загрузке", region, path)
        return NightChoice()
    if night.region != region:
        logger.info("Ночной план региона %s не подошёл: файл посчитан для региона %s", region, night.region)
        return NightChoice()
    plan, problem_text = routes_plan(problem, night.routes)
    if problem_fingerprint(problem, weights) != night.fingerprint:
        if plan is None:
            logger.info(
                "Ночной план региона %s не подошёл: отпечаток задачи другой, а его маршруты на этой задаче "
                "недопустимы (%s); план ищется с нуля",
                region,
                problem_text,
            )
            return NightChoice()
        logger.info(
            "Ночной план региона %s не подошёл: отпечаток задачи другой (нагрузка, обед, заявки, бригады или "
            "матрицы не те, что ночью); поиск стартует от его маршрутов",
            region,
        )
        return NightChoice(seed=plan)
    if plan is None:
        logger.warning(
            "Ночной план региона %s не подошёл: отпечаток совпал, но маршруты не проходят проверку (%s); "
            "план ищется с нуля",
            region,
            problem_text,
        )
        return NightChoice()
    if plan.metrics.assigned != night.metrics.assigned:
        logger.warning(
            "Ночной план региона %s не подошёл: назначает %d заявок, а в файле записано %d; поиск стартует "
            "от его маршрутов",
            region,
            plan.metrics.assigned,
            night.metrics.assigned,
        )
        return NightChoice(seed=plan)
    precomputed = night.precomputed()
    logger.info(
        "Ночной план региона %s взят утренним планом без поиска: отпечаток задачи совпал, поиск %g мин, "
        "посчитан %s",
        region,
        round(precomputed.search_minutes, 1),
        night.computed_at,
    )
    return NightChoice(plan=plan, precomputed=precomputed)
