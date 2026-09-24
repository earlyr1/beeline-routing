"""Что сервис умеет писать и читать про день диспетчера, не зная, где день лежит.

Реализаций две. MemoryStateRepo (app/state/memory.py) не сохраняет ничего: это сегодняшний сервис, день живёт
в памяти процесса и уходит вместе с ней. PostgresStateRepo (app/state/postgres.py) кладёт день в базу, и
перезапуск backend его не теряет. Какую брать, решает DATABASE_URL в build_deps.

Слой API про репозиторий не знает: routes.py, timeline.py и proposals.py меняют состояние через методы
DatasetRecord, а уже они под той же блокировкой зовут DayWriter. Поэтому запись в базу идёт ровно там же,
где меняется память, и разойтись они не могут.

Ключевое решение схемы — планы ХРАНЯТСЯ, а не выводятся заново. Солвер ограничен по времени, два запуска на
одной задаче дают разные маршруты, поэтому «восстановим день, повторив события» показало бы диспетчеру другой
план, чем минуту назад. Единица хранения — запись кэша шагов таймлайна (app/planning/timeline.py): ключ шага,
готовая сессия после него. Утренний план — тот же кэш с пустым ключом.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from app.api.schemas import AgreedWindow, UploadReport
from app.llm.schemas import Proposal
from app.planning.models import EventVariant
from app.planning.session import PlanningSession
from app.planning.timeline import StepKey, TimelineEntry, TimelineStep

if TYPE_CHECKING:  # PreparedDay живёт рядом с реестром, а реестр держит DayWriter: импорт только для типов.
    from app.api.registry import PreparedDay


class StateConflict(RuntimeError):
    """День изменил кто-то извне процесса: ожидаемая ревизия шкалы не совпала с той, что в базе.

    При одном процессе uvicorn не случается никогда. Нужна на случай второй реплики backend: тихая порча
    состояния превращается во внятный отказ (HTTP 409).
    """


class StateUnavailable(RuntimeError):
    """Не удалось сохранить: база недоступна. Чтения идут из памяти процесса и работают дальше (HTTP 503)."""


class StateBroken(StateUnavailable):
    """База на месте, но правку не приняла: ошибка данных, а не связи.

    Отдельный класс нужен, чтобы «база недоступна» не значилось на совершенно здоровой базе: повтор запроса
    тут не поможет, и в логе такую ошибку ищут по-другому. Наследование от StateUnavailable оставляет прежним
    и ответ (HTTP 503), и откат правки из памяти (DatasetRecord._saved).
    """


class StateMissing(StateUnavailable):
    """Дня в хранилище больше нет: его вытеснили новые загрузки, а процесс всё ещё держит его в памяти.

    Писать такому дню некуда, и молчать об этом нельзя: половина правок задевала бы ноль строк и возвращалась
    успехом. Тоже HTTP 503 — с текстом, который говорит, что делать (загрузить файл заново).
    """


@dataclass
class DayState:
    """День, поднятый из хранилища: из него реестр собирает обычный DatasetRecord.

    Объекты здесь уже готовые (PreparedDay, PlanningSession, TimelineEntry): разбором jsonb занимается
    app/state/codec.py, а слой API получает ровно то же, что было бы у него без базы.
    """

    dataset_id: str
    status: str = "processing"
    stage: str = "parsing"
    error: str | None = None
    report: UploadReport | None = None
    prepared: PreparedDay | None = None
    # Утренний план дня: от него считаются шаги шкалы.
    base: PlanningSession | None = None
    entries: list[TimelineEntry] = field(default_factory=list)
    steps: dict[StepKey, TimelineStep] = field(default_factory=dict)
    cursor: int = 0
    revision: int = 0
    day_revision: int = 0
    last_number: int = 0
    last_version: int = 0
    agreed: dict[str, AgreedWindow] = field(default_factory=dict)
    proposals: list[Proposal] = field(default_factory=list)
    urgent_number: int = 0


class DayWriter(Protocol):
    """Ручка одного дня: её держит DatasetRecord, а SQL про неё знает только реализация.

    Все методы вызываются под record.lock, то есть по одному на день. Каждый из них — одна логическая правка
    и одна транзакция: падение процесса посреди неё не оставляет день в середине.
    """

    def save_status(self, *, status: str, stage: str, report: UploadReport | None, error: str | None) -> None:
        """Статус предподсчёта и отчёт разбора. Счётчик адресов (done/total) не сохраняется: он тикает часто,
        а день, пойманный перезапуском на предподсчёте, всё равно не возобновляется."""

    def save_day(
        self,
        prepared: PreparedDay,
        session: PlanningSession,
        *,
        revision: int,
        day_revision: int,
        last_number: int,
        last_version: int,
    ) -> None:
        """День с нуля: входные данные, утренний план, пустая шкала, текущее время 00:00, обзвона нет."""

    def save_cursor(self, cursor: int) -> None:
        """Текущее время плана."""

    def add_entry(self, entry: TimelineEntry, *, revision: int, expect: int) -> None:
        """Новое событие на шкале."""

    def drop_entry(self, entry_id: str, keys: Collection[StepKey], *, revision: int, expect: int) -> None:
        """Событие уходит со шкалы вместе с шагами, в которых участвовало; keys — что осталось в кэше."""

    def set_variant(self, entry_id: str, variant: EventVariant, *, revision: int, expect: int) -> None:
        """Выбор или смена стратегии события."""

    def bump_number(self, last_number: int) -> None:
        """Номер последнего созданного события: tl_<n> не повторяются и после перезапуска."""

    def add_step(self, key: StepKey, step: TimelineStep, *, last_version: int) -> None:
        """Посчитанный план шага. Повторная запись того же ключа ничего не меняет: остаётся первый план —
        тот, который видел диспетчер. Солвер недетерминирован, и пересчёт не имеет права его затереть.
        Тем же правилом живёт кэш в памяти (Timeline.store): контракт у двух хранилищ один."""

    def keep_steps(self, keys: Collection[StepKey]) -> None:
        """Оставляет в кэше только эти шаги (плюс утренний план): то же, что делает Timeline.prune."""

    def save_agreed(self, request_id: str, window: AgreedWindow) -> None:
        """Согласованное окно вкладки «Коммуникации»: что клиенту сказали по телефону."""

    def drop_agreed(self, request_id: str) -> None:
        """Отметка «договорились» снята."""

    def save_proposals(self, proposals: Collection[Proposal], *, urgent_number: int) -> None:
        """Предложения помощника целиком: их немного, а статусы меняются по одному."""


class StateRepo(Protocol):
    def create(self, dataset_id: str, *, keep: Collection[str] = ()) -> DayWriter:
        """Новый день в хранилище и ручка записи к нему.

        keep — дни, которые процесс всё ещё держит в памяти: хранилище не имеет права вытеснить их новым
        днём, иначе запись пошла бы в день, которого там уже нет.
        """

    def writer(self, dataset_id: str) -> DayWriter:
        """Ручка записи к дню, который уже есть."""

    def load(self, dataset_id: str) -> DayState | None:
        """День из хранилища или None, если такого дня в нём нет."""

    def close(self) -> None: ...
