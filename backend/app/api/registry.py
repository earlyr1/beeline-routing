"""Датасеты в памяти процесса: статус предподсчёта, план начала дня, таймлайн и план на текущее время."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.api.schemas import DatasetStatus, Progress, UploadReport
from app.domain.enums import EventType
from app.domain.models import Cancellation, Engineer, Event, Office, Plan, Request
from app.planning.session import PlanningSession
from app.planning.timeline import Timeline


@dataclass
class PreparedDay:
    region: str
    region_title: str
    office: Office
    requests: list[Request]
    engineers: list[Engineer]
    control: Plan | None
    # Отмены клиентов в течение дня: с них начинается шкала каждого собранного дня.
    cancellations: list[Cancellation] = field(default_factory=list)


@dataclass
class DatasetRecord:
    dataset_id: str
    status: str = "processing"
    stage: str = "parsing"
    done: int = 0
    total: int = 0
    report: UploadReport | None = None
    error: str | None = None
    prepared: PreparedDay | None = None
    # План на текущее время: план начала дня и все события таймлайна не позже cursor.
    session: PlanningSession | None = None
    # План начала дня (предподсчёт загрузки или пересборка): от него считаются шаги таймлайна.
    base: PlanningSession | None = None
    # Текущее время плана, минуты от полуночи.
    cursor: int = 0
    timeline: Timeline = field(default_factory=Timeline)
    # Ревизия таймлайна сразу после сборки дня: по ней видно, что на шкале только отмены дня.
    day_revision: int = 0
    # Короткая блокировка: чтение и замена session, cursor и событий таймлайна. Солвер под ней не работает.
    lock: threading.RLock = field(default_factory=threading.RLock)
    # Очередь изменений таймлайна, переносов времени с пересчётом и всех решений солвера. Берётся раньше lock.
    timeline_lock: threading.RLock = field(default_factory=threading.RLock)
    # Номер последнего нового плана датасета: номера планов не повторяются, в том числе после удаления событий.
    last_version: int = 0
    # Ревизия таймлайна, для которой уже запущен фоновый предподсчёт.
    precompute_revision: int | None = None

    def status_model(self) -> DatasetStatus:
        with self.lock:
            return DatasetStatus(
                dataset_id=self.dataset_id,
                status=self.status,
                stage=self.stage,
                progress=Progress(done=self.done, total=self.total),
                report=self.report,
                error=self.error,
            )

    def start_day(self, session: PlanningSession, cancellations: Sequence[Cancellation] = ()) -> None:
        """План дня с нуля: таймлайн очищается, текущее время 00:00. Номера событий tl_<n> не начинаются заново.

        На пустую шкалу по порядку встают отмены дня: заявку клиент отменяет в течение дня, поэтому в плане начала
        дня она есть, а её отмена ждёт своего времени. Это обычные события шкалы: диспетчер может их удалить.
        """
        with self.lock:
            self.base = session
            self.session = session
            self.cursor = 0
            self.timeline.clear()
            for cancellation in sorted(cancellations, key=lambda item: (item.time, item.request_id)):
                event = Event(
                    type=EventType.CANCEL, time=cancellation.time, request_id=cancellation.request_id
                )
                self.timeline.insert(self.timeline.create(event))
            self.day_revision = self.timeline.revision
            self.last_version = max(self.last_version, session.version)

    def day_unchanged(self) -> bool:
        """Шкала не менялась со сборки дня: на ней только отмены дня, и пересобирать день незачем."""
        with self.lock:
            return self.timeline.revision == self.day_revision

    def next_version(self) -> int:
        """Номер для следующего нового плана. Номер занимает use_version: отклонённое событие номера не тратит.

        Новые планы считаются по очереди под timeline_lock, поэтому два расчёта не получат один номер.
        """
        with self.lock:
            return self.last_version + 1

    def use_version(self, version: int) -> None:
        with self.lock:
            self.last_version = max(self.last_version, version)


class DatasetRegistry:
    def __init__(self) -> None:
        self._items: dict[str, DatasetRecord] = {}
        self._lock = threading.Lock()

    def create(self) -> DatasetRecord:
        record = DatasetRecord(dataset_id=f"d_{uuid.uuid4().hex[:8]}")
        with self._lock:
            self._items[record.dataset_id] = record
        return record

    def get(self, dataset_id: str) -> DatasetRecord | None:
        with self._lock:
            return self._items.get(dataset_id)
