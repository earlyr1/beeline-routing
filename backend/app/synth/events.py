"""Готовые события для демо: отмена, недоступность инженера, срочная заявка.

Если передан оптимизированный план, события выбираются так, чтобы менять его заметно:
отменяется заявка с окном не раньше времени события, стоящая в плане, недоступным становится
инженер с наибольшим числом визитов после этого времени. Заявку с таким окном не начнут до события
ни при каком лимите поиска, поэтому отмену примут и в демо с другим лимитом.
"""

from __future__ import annotations

import random
from collections import Counter

from app.domain.enums import EventType, Priority, Skill, Transport
from app.domain.models import Event, Plan, Request
from app.domain.timeutil import DAY_MIN
from app.ingest.beeline_csv import RawFile
from app.synth.config import SynthConfig

URGENT_REQUEST_ID = "URG-001"


def build_demo_events(
    cfg: SynthConfig,
    region: str,
    requests: list[Request],
    control: RawFile,
    synthetic: RawFile,
    crew_to_engineer: dict[str, str],
    plan: Plan | None = None,
) -> list[Event]:
    at = cfg.event_time
    located = {r.id: r for r in requests if r.lat is not None}
    events: list[Event] = []

    cancelled = [
        s.request_id
        for c, s in zip(control.rows, synthetic.rows, strict=True)
        if c.status_bk in cfg.cancelled_control_statuses and s.request_id in located
    ]
    planned_start = {v.request_id: v.start for route in plan.routes for v in route.visits} if plan else {}
    later = [rid for rid in cancelled if located[rid].window_start >= at]
    planned_later = [rid for rid in cancelled if planned_start.get(rid, -1) >= at]
    candidates = [rid for rid in later if rid in planned_start] or later or planned_later or cancelled
    if candidates:
        events.append(Event(type=EventType.CANCEL, time=at, request_id=candidates[0]))

    engineer_id = _busiest_in_plan(plan, at) if plan is not None else None
    if engineer_id is None:
        crews = Counter(row.crew for row in control.rows if row.crew in crew_to_engineer)
        if crews:
            engineer_id = crew_to_engineer[min(crews.items(), key=lambda item: (-item[1], item[0]))[0]]
    if engineer_id is not None:
        events.append(Event(type=EventType.ENGINEER_UNAVAILABLE, time=at, engineer_id=engineer_id))

    if located:
        base = random.Random(f"{cfg.seed}:urgent:{region}").choice(
            sorted(located.values(), key=lambda r: r.id)
        )
        events.append(
            Event(
                type=EventType.URGENT,
                time=at,
                request=Request(
                    id=URGENT_REQUEST_ID,
                    address=base.address,
                    lat=base.lat,
                    lon=base.lon,
                    geocode_precision=base.geocode_precision,
                    district=base.district,
                    duration_min=cfg.urgent_event.duration_min,
                    window_start=at,
                    window_end=min(DAY_MIN - 1, at + cfg.urgent_event.window_min),
                    priority=Priority.URGENT,
                    skill=Skill.EMERGENCY,
                    transport_required=Transport.CAR,
                    source_type_bk="Глобальная проблема",
                    source_type_hd="Авария",
                ),
            )
        )
    return events


def _busiest_in_plan(plan: Plan, at: int) -> str | None:
    counts = {route.engineer_id: sum(1 for v in route.visits if v.start >= at) for route in plan.routes}
    if not counts:
        return None
    engineer_id, count = min(counts.items(), key=lambda item: (-item[1], item[0]))
    return engineer_id if count > 0 else None
