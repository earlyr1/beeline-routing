"""Предложения помощника по датасетам: в памяти процесса и, если день хранится, в базе рядом с ним."""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection, Sequence

from app.llm.interpret import ProposalDraft
from app.llm.schemas import Proposal
from app.state.repo import DayWriter

# Ручка записи дня по его номеру: её даёт хранилище (app/state/repo.py). None — день никуда не сохраняется.
Writers = Callable[[str], DayWriter]


class ProposalStore:
    def __init__(self, writers: Writers | None = None) -> None:
        self._lock = threading.Lock()
        self._writers = writers
        self._items: dict[str, list[Proposal]] = {}
        self._urgent_numbers: dict[str, int] = {}

    def _persist(self, dataset_id: str) -> None:
        """Под self._lock: отдаёт хранилищу предложения дня целиком. Их немного, а меняются они по одному."""
        if self._writers is None:
            return
        self._writers(dataset_id).save_proposals(
            self._items.get(dataset_id, ()), urgent_number=self._urgent_numbers.get(dataset_id, 0)
        )

    def restore(self, dataset_id: str, proposals: Sequence[Proposal], urgent_number: int) -> None:
        """Предложения дня, поднятого из хранилища: в базу они обратно не пишутся."""
        with self._lock:
            self._items[dataset_id] = list(proposals)
            self._urgent_numbers[dataset_id] = urgent_number

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
            if created:
                self._persist(dataset_id)
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
                    self._persist(dataset_id)
                    return
            items.append(proposal)
            self._persist(dataset_id)

    def urgent_id_factory(self, dataset_id: str, taken: Collection[str]) -> Callable[[], str]:
        """Номера срочных заявок из чата: URG-AI-001, URG-AI-002… без повторов в датасете."""

        def next_id() -> str:
            with self._lock:
                while True:
                    number = self._urgent_numbers.get(dataset_id, 0) + 1
                    self._urgent_numbers[dataset_id] = number
                    candidate = f"URG-AI-{number:03d}"
                    if candidate not in taken:
                        self._persist(dataset_id)
                        return candidate

        return next_id
