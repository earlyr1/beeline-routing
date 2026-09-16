"""Заявки из синтетического файла + досинтез длительности, приоритета и транспорта."""

from __future__ import annotations

import random
from collections.abc import Callable

from app.domain.enums import Priority, Skill, Transport
from app.domain.models import Request
from app.ingest.beeline_csv import RawFile
from app.ingest.geocode import GeoResult
from app.synth.config import SynthConfig


def synth_duration(cfg: SynthConfig, request_id: str, type_bk: str) -> int:
    """Плановое время работ на адресе по официальному нормативу типа заявки BK (config/synth_config.yaml).

    Норматив — точное число, поэтому при duration_jitter = 0 возвращаем значение таблицы как есть,
    без округления: округлять нечего, а округление внесло бы расхождение с нормативом.
    """
    base = cfg.duration_by_bk.get(type_bk, cfg.default_duration_min)
    if not cfg.duration_jitter:
        return base
    rng = random.Random(f"{cfg.seed}:duration:{request_id}")
    value = base * (1 + rng.uniform(-cfg.duration_jitter, cfg.duration_jitter))
    step = cfg.duration_round_to
    return max(step, round(value / step) * step)


def synth_transport_required(cfg: SynthConfig, skill: Skill, type_hd: str) -> Transport | None:
    for rule in cfg.transport_required_rules:
        if rule.skill is not None and rule.skill != skill:
            continue
        if rule.hd_contains is not None and rule.hd_contains not in type_hd:
            continue
        return rule.transport
    return None


def synth_needs_equipment(cfg: SynthConfig, type_hd: str, connection: str) -> bool:
    """Нужно ли везти единицу оборудования: роутер, приставку или колонку.

    Правило без случайности: тип работ HD из equipment_hd_types или непустая колонка «Подключение» (FMC, FTTB —
    клиенту настраивают подключение, значит инженер везёт оборудование).
    """
    return type_hd in cfg.equipment_hd_types or bool(connection.strip())


def check_alignment(synthetic: RawFile, control: RawFile) -> None:
    """Синтетический и контрольный файлы описывают одни и те же заявки построчно."""
    problems: list[str] = []
    if len(synthetic.rows) != len(control.rows):
        problems.append(f"разное число заявок: {len(synthetic.rows)} и {len(control.rows)}")
    for s_row, c_row in zip(synthetic.rows, control.rows, strict=False):
        same = (
            s_row.window_start == c_row.window_start
            and s_row.window_end == c_row.window_end
            and s_row.type_hd == c_row.type_hd
            and c_row.address.startswith(s_row.address)
        )
        if not same:
            problems.append(
                f"строка {s_row.row_index + 1}: {s_row.request_id} не совпадает с {c_row.request_id}"
            )
    ids = [row.request_id for row in synthetic.rows]
    duplicates = sorted({request_id for request_id in ids if ids.count(request_id) > 1})
    if duplicates:
        problems.append(f"повторяются номера заявок: {', '.join(duplicates)}")
    if problems:
        raise ValueError("Файлы региона не согласованы: " + "; ".join(problems[:10]))


def build_requests(
    cfg: SynthConfig,
    synthetic: RawFile,
    control: RawFile | None,
    geocode: Callable[[str, str], GeoResult],
) -> list[Request]:
    statuses = [row.status_bk for row in control.rows] if control is not None else None
    requests: list[Request] = []
    for row in synthetic.rows:
        skill = cfg.skill_by_bk.get(row.type_bk)
        if skill is None:
            raise ValueError(f"Неизвестный тип заявки BK «{row.type_bk}» (заявка {row.request_id})")
        status = statuses[row.row_index] if statuses is not None else ""
        urgent = row.type_bk in cfg.urgent_bk_types or status in cfg.urgent_control_statuses
        geo = geocode(row.address, row.district)
        requests.append(
            Request(
                id=row.request_id,
                address=row.address,
                lat=geo.lat,
                lon=geo.lon,
                geocode_precision=geo.precision,
                district=row.district,
                duration_min=synth_duration(cfg, row.request_id, row.type_bk),
                window_start=row.window_start,
                window_end=row.window_end,
                priority=Priority.URGENT if urgent else Priority.NORMAL,
                skill=skill,
                transport_required=synth_transport_required(cfg, skill, row.type_hd),
                source_type_bk=row.type_bk,
                source_type_hd=row.type_hd,
                needs_equipment=synth_needs_equipment(cfg, row.type_hd, row.connection),
            )
        )
    return requests
