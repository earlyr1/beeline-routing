"""Инженеры: по одному на бригаду из контрольного распределения."""

from __future__ import annotations

import random
from collections import defaultdict

from app.domain.enums import Skill, Transport
from app.domain.models import Engineer, Office
from app.geo.haversine import haversine_km
from app.ingest.beeline_csv import RawFile, RawRequestRow
from app.synth.config import ShiftTemplate, SynthConfig
from app.synth.requests import synth_transport_required

SHIFT_TAIL_MIN = 30  # заявка «покрыта» сменой, если в окне остаётся хотя бы полчаса смены


def crew_histories(control: RawFile) -> dict[str, list[RawRequestRow]]:
    histories: dict[str, list[RawRequestRow]] = defaultdict(list)
    for row in control.rows:
        if row.crew:
            histories[row.crew].append(row)
    return dict(histories)


def choose_shift(cfg: SynthConfig, rows: list[RawRequestRow]) -> ShiftTemplate:
    def covered(shift: ShiftTemplate) -> int:
        return sum(
            1
            for row in rows
            if max(row.window_start, shift.start) <= min(row.window_end, shift.end - SHIFT_TAIL_MIN)
        )

    return max(cfg.shifts, key=covered)  # max возвращает первый из равных


def largest_remainder(shares: dict[Transport, float], total: int) -> dict[Transport, int]:
    weight = sum(shares.values())
    raw = {t: shares.get(t, 0.0) / weight * total for t in Transport}
    counts = {t: int(raw[t]) for t in Transport}
    left = total - sum(counts.values())
    for t in sorted(Transport, key=lambda item: raw[item] - counts[item], reverse=True)[:left]:
        counts[t] += 1
    return counts


def historical_car_crews(cfg: SynthConfig, histories: dict[str, list[RawRequestRow]]) -> set[str]:
    """Бригады, чьи реальные заявки по правилам конфига требовали автомобиль."""
    return {
        name
        for name, rows in histories.items()
        if any(
            synth_transport_required(cfg, cfg.skill_by_bk[row.type_bk], row.type_hd) == Transport.CAR
            for row in rows
        )
    }


def assign_transports(
    cfg: SynthConfig,
    region: str,
    engineers: list[tuple[str, set[Skill]]],
    forced_car_ids: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Transport]:
    total = len(engineers)
    counts = largest_remainder(cfg.transport_mix, total)
    if total >= len(Transport):
        for t in Transport:
            while counts[t] == 0:
                donor = max(counts, key=counts.get)
                counts[donor] -= 1
                counts[t] += 1
    forced = [
        eid for eid, skills in engineers if skills & set(cfg.force_car_for_skills) or eid in forced_car_ids
    ]
    deficit = len(forced) - counts[Transport.CAR]
    for t in sorted((t for t in Transport if t != Transport.CAR), key=counts.get, reverse=True):
        while deficit > 0 and counts[t] > 1:
            counts[t] -= 1
            counts[Transport.CAR] += 1
            deficit -= 1
    if deficit > 0:
        raise ValueError(f"Регион {region}: не хватает автомобилей для бригад, которым он обязателен")
    pool = [t for t in Transport for _ in range(counts[t])]
    for _ in forced:
        pool.remove(Transport.CAR)
    random.Random(f"{cfg.seed}:transport:{region}").shuffle(pool)
    rest = [eid for eid, _ in engineers if eid not in forced]
    result = {eid: Transport.CAR for eid in forced}
    result.update(zip(rest, pool, strict=True))
    return result


def history_medoid(
    rows: list[RawRequestRow], row_points: dict[int, tuple[float, float]]
) -> tuple[float, float] | None:
    """Адрес из истории бригады с минимальной суммой расстояний до остальных её адресов."""
    points = [row_points[row.row_index] for row in rows if row.row_index in row_points]
    if not points:
        return None
    return min(points, key=lambda p: sum(haversine_km(*p, *q) for q in points))


def build_engineers(
    cfg: SynthConfig,
    region: str,
    control: RawFile,
    office: Office,
    row_points: dict[int, tuple[float, float]] | None = None,
) -> tuple[list[Engineer], dict[str, str]]:
    histories = crew_histories(control)
    names = sorted(histories)
    ids = {name: f"E{k + 1:02d}" for k, name in enumerate(names)}
    order = list(Skill)
    skills = {
        name: sorted({cfg.skill_by_bk[row.type_bk] for row in histories[name]}, key=order.index)
        for name in names
    }
    forced_car_ids = (
        {ids[name] for name in historical_car_crews(cfg, histories)} if cfg.transport_from_history else set()
    )
    transports = assign_transports(
        cfg, region, [(ids[name], set(skills[name])) for name in names], forced_car_ids
    )
    engineers = []
    for name in names:
        shift = choose_shift(cfg, histories[name])
        start = (office.lat, office.lon)
        if cfg.engineer_start == "history_medoid" and row_points:
            start = history_medoid(histories[name], row_points) or start
        engineers.append(
            Engineer(
                id=ids[name],
                name=name,
                start_lat=start[0],
                start_lon=start[1],
                shift_start=shift.start,
                shift_end=shift.end,
                skills=skills[name],
                transport=transports[ids[name]],
            )
        )
    return engineers, ids
