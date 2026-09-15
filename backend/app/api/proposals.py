"""Чат с помощником и рекомендуемые изменения: модель предлагает, диспетчер подтверждает."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import AppDeps
from app.api.registry import DatasetRecord
from app.api.routes import Deps, _record, _session
from app.api.schemas import PlanningState, to_planning_state
from app.domain.models import Event
from app.llm.client import LlmError
from app.llm.interpret import NOTHING_FOUND, interpret
from app.llm.prompt import build_messages
from app.llm.schemas import STATUS_DONE_RU, ChatRequest, ChatResponse, Proposal
from app.planning.session import EventRejected, PlanningSession, apply_event

router = APIRouter(prefix="/api/datasets/{dataset_id}")

LLM_NOT_CONFIGURED = "Помощник не настроен: задайте LLM_BASE_URL и LLM_MODEL в .env и перезапустите backend."


class ApproveResponse(BaseModel):
    proposal: Proposal
    state: PlanningState


class ApproveAllResponse(BaseModel):
    proposals: list[Proposal]
    state: PlanningState


def _pending(deps: AppDeps, dataset_id: str, proposal_id: str) -> Proposal:
    proposal = deps.proposals.get(dataset_id, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"Предложение {proposal_id} не найдено.")
    if proposal.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"Предложение {proposal_id} уже {STATUS_DONE_RU[proposal.status]}."
        )
    return proposal


def _refreshed(event: Event, session: PlanningSession) -> Event:
    """Предложение, созданное до других событий, применяется не раньше текущего времени плана."""
    return event if event.time >= session.now else event.model_copy(update={"time": session.now})


def _approve(deps: AppDeps, record: DatasetRecord, proposal: Proposal) -> Proposal:
    """Применяет одно предложение через общий конвейер событий. Вызывать под record.lock."""
    session = _session(record)
    event = _refreshed(proposal.event, session)
    try:
        updated = apply_event(session, event, deps.ingest.planning)
    except EventRejected as error:
        result = proposal.model_copy(update={"status": "failed", "event": event, "error": str(error)})
    else:
        record.session = updated
        result = proposal.model_copy(
            update={"status": "approved", "event": event, "result_diff": updated.last_diff, "error": None}
        )
    deps.proposals.save(record.dataset_id, result)
    return result


@router.post("/chat", response_model=ChatResponse)
def chat(dataset_id: str, body: ChatRequest, deps: Deps) -> ChatResponse:
    if deps.llm is None:
        raise HTTPException(status_code=503, detail=LLM_NOT_CONFIGURED)
    record = _record(deps, dataset_id)
    with record.lock:
        session = _session(record)
    try:
        result = deps.llm.complete(build_messages(body.text, session))
    except LlmError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    taken = {request.id for request in session.requests} | {
        p.event.request.id for p in deps.proposals.for_dataset(dataset_id) if p.event.request is not None
    }
    interpretation = interpret(
        result, session, deps.ingest.planning, deps.proposals.urgent_id_factory(dataset_id, taken)
    )
    proposals = deps.proposals.create(dataset_id, interpretation.drafts, body.text, session.version)
    clarification = "\n".join(interpretation.clarifications) or None
    if not proposals and clarification is None:
        clarification = NOTHING_FOUND
    return ChatResponse(proposals=proposals, clarification=clarification)


@router.get("/proposals", response_model=list[Proposal])
def list_proposals(dataset_id: str, deps: Deps) -> list[Proposal]:
    _record(deps, dataset_id)
    return deps.proposals.for_dataset(dataset_id)


@router.post("/proposals/approve-all", response_model=ApproveAllResponse)
def approve_all(dataset_id: str, deps: Deps) -> ApproveAllResponse:
    record = _record(deps, dataset_id)
    with record.lock:
        _session(record)
        for proposal in deps.proposals.for_dataset(dataset_id):
            if proposal.status == "pending":
                _approve(deps, record, proposal)
        return ApproveAllResponse(
            proposals=deps.proposals.for_dataset(dataset_id), state=to_planning_state(record.session)
        )


@router.post("/proposals/reject-all", response_model=list[Proposal])
def reject_all(dataset_id: str, deps: Deps) -> list[Proposal]:
    record = _record(deps, dataset_id)
    with record.lock:
        for proposal in deps.proposals.for_dataset(dataset_id):
            if proposal.status == "pending":
                deps.proposals.save(dataset_id, proposal.model_copy(update={"status": "rejected"}))
        return deps.proposals.for_dataset(dataset_id)


@router.post("/proposals/{proposal_id}/approve", response_model=ApproveResponse)
def approve(dataset_id: str, proposal_id: str, deps: Deps) -> ApproveResponse:
    record = _record(deps, dataset_id)
    with record.lock:
        _session(record)
        result = _approve(deps, record, _pending(deps, dataset_id, proposal_id))
        return ApproveResponse(proposal=result, state=to_planning_state(record.session))


@router.post("/proposals/{proposal_id}/reject", response_model=Proposal)
def reject(dataset_id: str, proposal_id: str, deps: Deps) -> Proposal:
    record = _record(deps, dataset_id)
    with record.lock:
        result = _pending(deps, dataset_id, proposal_id).model_copy(update={"status": "rejected"})
        deps.proposals.save(dataset_id, result)
        return result
