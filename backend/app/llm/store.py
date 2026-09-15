"""Предложения по датасетам в памяти процесса."""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection

from app.llm.interpret import ProposalDraft
from app.llm.schemas import Proposal


class ProposalStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, list[Proposal]] = {}
        self._urgent_numbers: dict[str, int] = {}

    def create(
        self, dataset_id: str, drafts: list[ProposalDraft], source_text: str, version: int
    ) -> list[Proposal]:
        with self._lock:
            items = self._items.setdefault(dataset_id, [])
            created = []
            for draft in drafts:
                proposal = Proposal(
                    id=f"pr_{len(items) + 1}",
                    status="failed" if draft.error else "pending",
                    event=draft.event,
                    rationale=draft.rationale,
                    source_text=source_text,
                    created_at_version=version,
                    error=draft.error,
                )
                items.append(proposal)
                created.append(proposal)
            return created

    def for_dataset(self, dataset_id: str) -> list[Proposal]:
        with self._lock:
            return list(self._items.get(dataset_id, []))

    def get(self, dataset_id: str, proposal_id: str) -> Proposal | None:
        with self._lock:
            return next((p for p in self._items.get(dataset_id, []) if p.id == proposal_id), None)

    def save(self, dataset_id: str, proposal: Proposal) -> None:
        with self._lock:
            items = self._items.setdefault(dataset_id, [])
            for index, existing in enumerate(items):
                if existing.id == proposal.id:
                    items[index] = proposal
                    return
            items.append(proposal)

    def urgent_id_factory(self, dataset_id: str, taken: Collection[str]) -> Callable[[], str]:
        """Номера срочных заявок из чата: URG-AI-001, URG-AI-002… без повторов в датасете."""

        def next_id() -> str:
            with self._lock:
                while True:
                    number = self._urgent_numbers.get(dataset_id, 0) + 1
                    self._urgent_numbers[dataset_id] = number
                    candidate = f"URG-AI-{number:03d}"
                    if candidate not in taken:
                        return candidate

        return next_id
