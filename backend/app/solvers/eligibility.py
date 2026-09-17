"""Жёсткий фильтр «может ли инженер в принципе взять заявку»: закрепление, навык, транспорт, доступность."""

from __future__ import annotations

from enum import StrEnum

from app.domain.models import Request
from app.solvers.problem import EngineerState


class Exclusion(StrEnum):
    FIXED_TO_OTHER = "fixed_to_other"  # диспетчер закрепил заявку за другой бригадой
    NO_SKILL = "no_skill"
    NO_TRANSPORT = "no_transport"
    UNAVAILABLE = "unavailable"


def exclusion(request: Request, state: EngineerState) -> Exclusion | None:
    engineer = state.engineer
    if request.fixed_engineer_id is not None and request.fixed_engineer_id != engineer.id:
        return Exclusion.FIXED_TO_OTHER
    if request.skill not in engineer.skills:
        return Exclusion.NO_SKILL
    if request.transport_required is not None and engineer.transport != request.transport_required:
        return Exclusion.NO_TRANSPORT
    if not state.active:
        return Exclusion.UNAVAILABLE
    return None
