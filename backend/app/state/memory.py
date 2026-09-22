"""День живёт только в памяти процесса: ровно сегодняшний сервис.

Так работает всё без DATABASE_URL — тесты, запуск uvicorn руками, `docker compose up` без сервиса postgres.
Методы записи не делают ничего и ничего не сериализуют: горячий путь остаётся прежним, перезапуск теряет день,
и фронт показывает привычное «Прежний план недоступен: сервис перезапускался».
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

from app.api.schemas import AgreedWindow, UploadReport
from app.llm.schemas import Proposal
from app.planning.models import EventVariant
from app.planning.session import PlanningSession
from app.planning.timeline import StepKey, TimelineEntry, TimelineStep
from app.state.repo import DayState, DayWriter

if TYPE_CHECKING:
    from app.api.registry import PreparedDay


class NullDayWriter:
    """Ручка дня, который никуда не сохраняется."""

    def save_status(
        self, *, status: str, stage: str, report: UploadReport | None, error: str | None
    ) -> None: ...

    def save_day(
        self,
        prepared: PreparedDay,
        session: PlanningSession,
        *,
        revision: int,
        day_revision: int,
        last_number: int,
        last_version: int,
    ) -> None: ...

    def save_cursor(self, cursor: int) -> None: ...

    def add_entry(self, entry: TimelineEntry, *, revision: int, expect: int) -> None: ...

    def drop_entry(self, entry_id: str, keys: Collection[StepKey], *, revision: int, expect: int) -> None: ...

    def set_variant(self, entry_id: str, variant: EventVariant, *, revision: int, expect: int) -> None: ...

    def bump_number(self, last_number: int) -> None: ...

    def add_step(self, key: StepKey, step: TimelineStep, *, last_version: int) -> None: ...

    def keep_steps(self, keys: Collection[StepKey]) -> None: ...

    def save_agreed(self, request_id: str, window: AgreedWindow) -> None: ...

    def drop_agreed(self, request_id: str) -> None: ...

    def save_proposals(self, proposals: Collection[Proposal], *, urgent_number: int) -> None: ...


class MemoryStateRepo:
    """Хранилище, которого нет: реестр держит дни сам, а поднимать из него нечего."""

    def __init__(self) -> None:
        self._writer = NullDayWriter()

    def create(self, dataset_id: str) -> DayWriter:
        return self._writer

    def writer(self, dataset_id: str) -> DayWriter:
        return self._writer

    def load(self, dataset_id: str) -> DayState | None:
        return None

    def close(self) -> None: ...
