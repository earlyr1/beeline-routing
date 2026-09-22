"""Датасеты в памяти процесса: статус предподсчёта, план начала дня, таймлайн и план на текущее время."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from app.api.schemas import DatasetStatus, Progress, UploadReport
from app.domain.models import Engineer, Office, Plan, Request
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
    # Выгрузки Билайна по региону нет, день сгенерирован нами (docs/assumptions.md): об этом говорят и отчёт
    # предподсчёта, и вкладка «Сравнение», где «диспетчеры» такого региона — наша эвристика, а не решения людей.
    generated: bool = False


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
    # Ревизия таймлайна сразу после сборки дня: по ней видно, что шкалу с тех пор не трогали.
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

    def start_day(self, session: PlanningSession) -> None:
        """План дня с нуля: таймлайн пустой, текущее время 00:00. Номера событий tl_<n> не начинаются заново.

        Событий по умолчанию на шкале нет: отмены, срочные заявки и прочее диспетчер добавляет сам.
        """
        with self.lock:
            self.base = session
            self.session = session
            self.cursor = 0
            self.timeline.clear()
            self.day_revision = self.timeline.revision
            self.last_version = max(self.last_version, session.version)

    def day_unchanged(self) -> bool:
        """Шкала не менялась со сборки дня: она пустая, и пересобирать день незачем."""
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


# Сколько последних наборов данных живёт в памяти. Экран показывает один, прежние нужны только вкладке, которую
# не закрыли; каждый день — это заявки, матрица и планы, поэтому бесконечно копить их нельзя.
MAX_DATASETS = 8


class DatasetRegistry:
    def __init__(self) -> None:
        self._items: dict[str, DatasetRecord] = {}
        self._lock = threading.Lock()

    def create(self) -> DatasetRecord:
        """Новый набор данных. Самые давние забываются: их страницы всё равно никто не держит открытыми."""
        record = DatasetRecord(dataset_id=f"d_{uuid.uuid4().hex[:8]}")
        with self._lock:
            self._items[record.dataset_id] = record
            while len(self._items) > MAX_DATASETS:
                self._items.pop(next(iter(self._items)))
        return record

    def get(self, dataset_id: str) -> DatasetRecord | None:
        with self._lock:
            return self._items.get(dataset_id)
