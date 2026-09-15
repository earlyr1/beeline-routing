"""Предложения изменений плана от помощника (объект Proposal из API-контракта)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.models import Event
from app.planning.models import PlanDiff

ProposalStatus = Literal["pending", "approved", "rejected", "failed"]

STATUS_DONE_RU = {"approved": "применено", "rejected": "отклонено", "failed": "не применилось"}


class Proposal(BaseModel):
    id: str
    status: ProposalStatus
    event: Event
    rationale: str
    source_text: str
    created_at_version: int
    result_diff: PlanDiff | None = None
    error: str | None = None


class ChatRequest(BaseModel):
    text: str = Field(max_length=2000)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("сообщение пустое")
        return value


class ChatResponse(BaseModel):
    proposals: list[Proposal]
    clarification: str | None = None
