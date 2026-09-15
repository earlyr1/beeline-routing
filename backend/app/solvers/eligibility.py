"""Жёсткий фильтр «может ли инженер в принципе взять заявку»: навык, транспорт, доступность."""

from __future__ import annotations

from enum import StrEnum

from app.domain.models import Request
from app.solvers.problem import EngineerState


class Exclusion(StrEnum):
    NO_SKILL = "no_skill"
    NO_TRANSPORT = "no_transport"
    UNAVAILABLE = "unavailable"


def exclusion(request: Request, state: EngineerState) -> Exclusion | None:
    engineer = state.engineer
    if request.skill not in engineer.skills:
        return Exclusion.NO_SKILL
    if request.transport_required is not None and engineer.transport != request.transport_required:
        return Exclusion.NO_TRANSPORT
    if not state.active:
        return Exclusion.UNAVAILABLE
    return None
