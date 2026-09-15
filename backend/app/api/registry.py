"""Датасеты в памяти процесса: статус предподсчёта и текущая сессия планирования."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from app.api.schemas import DatasetStatus, Progress, UploadReport
from app.domain.models import Engineer, Office, Plan, Request
from app.planning.session import PlanningSession


@dataclass
class PreparedDay:
    region: str
    region_title: str
    office: Office
    requests: list[Request]
    engineers: list[Engineer]
    control: Plan | None


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
    session: PlanningSession | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

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
