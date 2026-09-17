"""Чат с помощником и рекомендуемые изменения: модель предлагает, диспетчер подтверждает."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import AppDeps
from app.api.registry import DatasetRecord
from app.api.routes import Deps, _record, _session
from app.api.schemas import PlanningState
from app.api.timeline import ensure_precompute, insert_and_replay, planning_state, settle
from app.domain.models import Event
from app.llm.client import LlmError
from app.llm.interpret import NOTHING_FOUND, interpret
from app.llm.prompt import build_messages
from app.llm.schemas import STATUS_DONE_RU, ChatRequest, ChatResponse, Proposal
from app.planning.variants import is_choosable

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


def _refreshed(event: Event, cursor: int) -> Event:
    """Предложение, созданное до других событий или переноса времени, применяется не раньше текущего времени плана."""
    return event if event.time >= cursor else event.model_copy(update={"time": cursor})


def _approve(deps: AppDeps, record: DatasetRecord, proposal: Proposal) -> Proposal:
    """Применяет одно предложение через таймлайн: событие встаёт на шкалу, текущее время переходит к нему.

    «Ломающее» событие сразу получает стратегию optimal. Вызывать под record.timeline_lock.
    """
    ctx = deps.ingest.planning
    with record.lock:
        _session(record)
        cursor = record.cursor
        event = _refreshed(proposal.event, cursor)
        entry = record.timeline.create(
            event, checked=True, variant="optimal" if is_choosable(event) else None
        )
    step = insert_and_replay(record, ctx, entry)
    if step is None:
        with record.lock:
            record.timeline.remove(entry.id)
        result = proposal.model_copy(
            update={
                "status": "failed",
                "event": event,
                "error": "Сначала выберите вариант для события на шкале.",
            }
        )
    elif step.applied is None:
        result = proposal.model_copy(update={"status": "failed", "event": event, "error": step.reason})
    else:
        settle(record, ctx, max(cursor, event.time))
        # Сохраняются событие и изменения из шага самого предложения: previous_request и previous_transport на
        # момент применения, а не на момент, когда помощник составил предложение.
        result = proposal.model_copy(
            update={
                "status": "approved",
                "event": step.applied.event,
                "result_diff": step.session.last_diff,
                "error": None,
            }
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
        cursor = record.cursor
        scheduled = {
            entry.event.request.id for entry in record.timeline.entries if entry.event.request is not None
        }
    try:
        result = deps.llm.complete(build_messages(body.text, session, now=cursor))
    except LlmError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    taken = (
        {request.id for request in session.requests}
        | scheduled
        | {p.event.request.id for p in deps.proposals.for_dataset(dataset_id) if p.event.request is not None}
    )
    interpretation = interpret(
        result,
        session,
        deps.ingest.planning,
        deps.proposals.urgent_id_factory(dataset_id, taken),
        now=cursor,
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
    try:
        with record.timeline_lock:
            with record.lock:
                _session(record)
            for proposal in deps.proposals.for_dataset(dataset_id):
                if proposal.status == "pending":
                    _approve(deps, record, proposal)
            return ApproveAllResponse(
                proposals=deps.proposals.for_dataset(dataset_id), state=planning_state(record)
            )
    finally:
        ensure_precompute(record, deps.ingest.planning, deps.run_background)


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
    try:
        with record.timeline_lock:
            with record.lock:
                _session(record)
            result = _approve(deps, record, _pending(deps, dataset_id, proposal_id))
            return ApproveResponse(proposal=result, state=planning_state(record))
    finally:
        ensure_precompute(record, deps.ingest.planning, deps.run_background)


@router.post("/proposals/{proposal_id}/reject", response_model=Proposal)
def reject(dataset_id: str, proposal_id: str, deps: Deps) -> Proposal:
    record = _record(deps, dataset_id)
    with record.lock:
        result = _pending(deps, dataset_id, proposal_id).model_copy(update={"status": "rejected"})
        deps.proposals.save(dataset_id, result)
        return result
