"""Датасеты процесса: статус предподсчёта, план начала дня, таймлайн и план на текущее время.

Запись в хранилище идёт отсюда. Слой API (routes.py, timeline.py, proposals.py) меняет состояние только
методами DatasetRecord, а они под той же record.lock зовут DayWriter — и правку, которую хранилище не приняло,
откатывают из памяти (_saved). Поэтому память и база не расходятся: день, «сохранившийся» только на экране,
хуже честного отказа. Без DATABASE_URL писателем работает пустышка (app/state/memory.py), и это ровно
сегодняшнее поведение.

Записи здесь — кэш перед хранилищем: /state опрашивается каждую секунду и не должен каждый раз поднимать день
из базы. Дня, которого нет в памяти, реестр ищет в хранилище и собирает из него обычный DatasetRecord.
"""

from __future__ import annotations

import threading
import uuid
import weakref
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.api.schemas import AgreedWindow, DatasetStatus, Progress, UploadReport
from app.domain.models import Engineer, Event, Office, Plan, Request
from app.ingest.geocode import GeoResult
from app.planning.models import EventVariant
from app.planning.session import PlanningSession
from app.planning.timeline import (
    StepKey,
    Timeline,
    TimelineEntry,
    TimelineStep,
    Walk,
    entry_token,
)
from app.state.memory import MemoryStateRepo, NullDayWriter
from app.state.repo import DayState, DayWriter, StateConflict, StateRepo, StateUnavailable


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
class _DaySnapshot:
    """День до правки: к нему память возвращается, если хранилище правку не приняло.

    Номер последнего события (Timeline.last_number) сюда не входит нарочно: занятый номер не возвращается,
    иначе tl_<n> налезли бы на ключи уже посчитанных шагов.
    """

    entries: list[TimelineEntry]
    steps: dict[StepKey, TimelineStep]
    revision: int
    base: PlanningSession | None
    session: PlanningSession | None
    prepared: PreparedDay | None
    cursor: int
    day_revision: int
    last_version: int
    agreed: dict[str, AgreedWindow]


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
    # Ревизия таймлайна, для которой уже запущен фоновый предподсчёт. None — не запущен ни для одной.
    precompute_revision: int | None = None
    # Сколько фоновых предподсчётов этого дня сейчас работает: пока хоть один, запись нельзя забывать
    # из памяти (см. _forget). Счётчик, а не флаг: предподсчёт новой ревизии начинается, пока прежний
    # ещё доходит до своей проверки ревизии, и снятый им флаг оставил бы второй без защиты.
    precomputing: int = 0
    # Время, на котором стояли часы до перезапуска, если план до него ещё не досчитан: часы стоят на времени
    # последнего посчитанного шага, а досчёт возвращает их сюда (см. _restore_plan и precompute).
    resume_cursor: int | None = None
    # Что уже согласовано с клиентами: номер заявки → окно, которое клиенту назвали по телефону.
    agreed: dict[str, AgreedWindow] = field(default_factory=dict)
    # Куда пишется день; пустышка — день живёт только в памяти процесса.
    store: DayWriter = field(default_factory=NullDayWriter)

    @contextmanager
    def _saved(self) -> Iterator[None]:
        """Под self.lock: правка, которую не приняло хранилище, не остаётся и в памяти.

        Снимок дешёвый — списки и словари копируются по ссылкам, а события и шаги неизменяемы. Ревизия
        возвращается вместе с ними: иначе память ушла бы вперёд базы навсегда, и каждая следующая правка
        шкалы получала бы «Данные изменились в другой вкладке» на ровном месте.
        """
        snapshot = _DaySnapshot(
            entries=list(self.timeline.entries),
            steps=dict(self.timeline.steps),
            revision=self.timeline.revision,
            base=self.base,
            session=self.session,
            prepared=self.prepared,
            cursor=self.cursor,
            day_revision=self.day_revision,
            last_version=self.last_version,
            agreed=dict(self.agreed),
        )
        try:
            yield
        except (StateUnavailable, StateConflict):
            self.timeline.entries = snapshot.entries
            self.timeline.steps = snapshot.steps
            self.timeline.revision = snapshot.revision
            self.base = snapshot.base
            self.session = snapshot.session
            self.prepared = snapshot.prepared
            self.cursor = snapshot.cursor
            self.day_revision = snapshot.day_revision
            self.last_version = snapshot.last_version
            self.agreed = snapshot.agreed
            raise

    def take_resume(self) -> int | None:
        """Время, к которому план должен догнать часы после подъёма дня из базы. Забирается один раз."""
        with self.lock:
            cursor, self.resume_cursor = self.resume_cursor, None
            return cursor

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

    def save_status(self) -> None:
        """Отдаёт хранилищу статус предподсчёта и отчёт разбора. Счётчик адресов не сохраняется."""
        with self.lock:
            self.store.save_status(status=self.status, stage=self.stage, report=self.report, error=self.error)

    def start_day(self, session: PlanningSession, prepared: PreparedDay | None = None) -> None:
        """План дня с нуля: таймлайн пустой, текущее время 00:00. Номера событий tl_<n> не начинаются заново.

        Событий по умолчанию на шкале нет: отмены, срочные заявки и прочее диспетчер добавляет сам.
        Обзвон тоже начинается заново: в новом дне клиентам ещё не звонили.
        """
        with self.lock, self._saved():
            if prepared is not None:
                self.prepared = prepared
            self.base = session
            self.session = session
            self.cursor = 0
            self.resume_cursor = None
            self.timeline.clear()
            self.day_revision = self.timeline.revision
            self.last_version = max(self.last_version, session.version)
            self.agreed = {}
            if self.prepared is not None:
                self.store.save_day(
                    self.prepared,
                    session,
                    revision=self.timeline.revision,
                    day_revision=self.day_revision,
                    last_number=self.timeline.last_number,
                    last_version=self.last_version,
                )

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

    def new_entry(
        self,
        event: Event,
        geo: Mapping[str, GeoResult] | None = None,
        *,
        checked: bool = False,
        variant: EventVariant | None = None,
    ) -> TimelineEntry:
        """Новое событие со следующим номером; на шкалу его ставит add_entry.

        Номер занят сразу: отклонённое событие его не возвращает, иначе после перезапуска номера tl_<n>
        налезли бы на ключи уже посчитанных шагов.
        """
        with self.lock:
            entry = self.timeline.create(event, geo, checked=checked, variant=variant)
            self.store.bump_number(self.timeline.last_number)
            return entry

    def add_entry(self, entry: TimelineEntry) -> int:
        """Ставит событие на шкалу и возвращает его место по порядку применения."""
        with self.lock, self._saved():
            revision = self.timeline.revision
            position = self.timeline.insert(entry)
            self.store.add_entry(entry, revision=self.timeline.revision, expect=revision)
            return position

    def drop_entry(self, entry_id: str) -> TimelineEntry | None:
        """Убирает событие со шкалы вместе с шагами, в которых оно участвовало."""
        with self.lock, self._saved():
            revision = self.timeline.revision
            entry = self.timeline.remove(entry_id)
            if entry is not None:
                self.store.drop_entry(
                    entry_id, self.timeline.steps.keys(), revision=self.timeline.revision, expect=revision
                )
            return entry

    def choose_variant(self, entry_id: str, variant: EventVariant) -> TimelineEntry | None:
        """Выбор или смена стратегии «ломающего» события."""
        with self.lock, self._saved():
            revision = self.timeline.revision
            entry = self.timeline.set_variant(entry_id, variant)
            if entry is not None:
                self.store.set_variant(entry_id, variant, revision=self.timeline.revision, expect=revision)
            return entry

    def store_step(
        self,
        walk: Walk,
        entry: TimelineEntry,
        step: TimelineStep,
        variant: EventVariant | None = None,
        *,
        version: int | None = None,
    ) -> None:
        """Кладёт посчитанный план шага в кэш и в хранилище. version — номер плана, если шаг принят."""
        with self.lock, self._saved():
            self.timeline.store(walk, entry, step, variant)
            if version is not None and step.applied is not None:
                self.use_version(version)
            self.store.add_step(
                (walk.prefix, entry_token(entry, variant)), step, last_version=self.last_version
            )

    def prune_steps(self, walk: Walk) -> None:
        """Убирает из кэша шаги прежних версий шкалы. Хранилище трогается, только если что-то убралось."""
        with self.lock, self._saved():
            before = len(self.timeline.steps)
            self.timeline.prune(walk)
            if len(self.timeline.steps) != before:
                self.store.keep_steps(self.timeline.steps.keys())

    def move_to(self, cursor: int, session: PlanningSession) -> None:
        """Переносит текущее время плана и ставит на него план."""
        with self.lock, self._saved():
            changed = cursor != self.cursor
            self.cursor = cursor
            self.session = session
            # Часы двигают либо диспетчер, либо досчёт: догонять прежнее время после этого не нужно.
            self.resume_cursor = None
            if changed:
                self.store.save_cursor(cursor)

    def mark_agreed(self, request_id: str, window: AgreedWindow) -> None:
        """Клиенту назвали окно (или сказали, что сегодня не приедем)."""
        with self.lock, self._saved():
            self.agreed[request_id] = window
            self.store.save_agreed(request_id, window)

    def unmark_agreed(self, request_id: str) -> bool:
        """Снимает отметку «договорились»; False — её и не было."""
        with self.lock, self._saved():
            if self.agreed.pop(request_id, None) is None:
                return False
            self.store.drop_agreed(request_id)
            return True


# Сколько последних наборов данных живёт в памяти. Экран показывает один, прежние нужны только вкладке, которую
# не закрыли; каждый день — это заявки, матрица и планы, поэтому бесконечно копить их нельзя. С Postgres забытый
# день не теряется: следующее обращение поднимет его из базы. А пока на забытую запись кто-то ссылается —
# идущий запрос, фоновый предподсчёт, — поднимать нечего: она и есть единственный экземпляр дня.
MAX_DATASETS = 8

# Что сделать с днём, поднятым из хранилища, кроме самого DatasetRecord: восстановить предложения помощника.
Hydrated = Callable[[DayState], None]


class DatasetRegistry:
    def __init__(self, store: StateRepo | None = None, hydrated: Hydrated | None = None) -> None:
        self._store: StateRepo = store or MemoryStateRepo()
        self._hydrated = hydrated
        self._items: dict[str, DatasetRecord] = {}
        # Забытые дни, на которые кто-то ещё ссылается: идущий запрос, фоновый предподсчёт. Пока живёт
        # сам объект, живёт и эта запись, а как только последняя ссылка ушла — исчезает и она.
        self._evicted: weakref.WeakValueDictionary[str, DatasetRecord] = weakref.WeakValueDictionary()
        self._lock = threading.Lock()

    def create(self) -> DatasetRecord:
        """Новый набор данных. Самые давние забываются: их страницы всё равно никто не держит открытыми."""
        dataset_id = f"d_{uuid.uuid4().hex[:8]}"
        record = DatasetRecord(dataset_id=dataset_id, store=self._store.create(dataset_id, keep=self._live()))
        with self._lock:
            self._items[dataset_id] = record
            self._forget()
        return record

    def _live(self) -> list[str]:
        """Дни, которые процесс держит в руках: хранилищу их вытеснять нельзя, писать станет некуда."""
        with self._lock:
            return [*self._items, *self._evicted]

    def get(self, dataset_id: str) -> DatasetRecord | None:
        """Набор данных из памяти, а если его там нет — из хранилища. None — такого дня нет нигде."""
        record = self._known(dataset_id)
        if record is not None:
            return record
        # Подъём дня читает и разбирает мегабайты, поэтому идёт без блокировки реестра: остальные запросы
        # в это время отвечают. Два запроса, поднявшие день одновременно, оставят одну запись.
        state = self._store.load(dataset_id)
        if state is None:
            return None
        record = _restored(state, self._store.writer(dataset_id))
        with self._lock:
            existing = self._items.get(dataset_id) or self._evicted.get(dataset_id)
            if existing is not None:
                return existing
            self._items[dataset_id] = record
            self._forget()
        if self._hydrated is not None:
            self._hydrated(state)
        return record

    def _known(self, dataset_id: str) -> DatasetRecord | None:
        """День, который у процесса уже есть, — в памяти реестра или у кого-то в руках.

        Забытая, но ещё живая запись возвращается в память и не поднимается из базы заново: двух экземпляров
        одного дня быть не должно. У них разошлись бы номера событий и ревизия шкалы, и следующая правка
        диспетчера упёрлась бы в «tl_1 уже есть» на ровном месте.
        """
        with self._lock:
            record = self._items.get(dataset_id)
            if record is not None:
                return record
            record = self._evicted.get(dataset_id)
            if record is not None:
                self._items[dataset_id] = record
                self._forget()
            return record

    def _forget(self) -> None:
        """Под self._lock: забывает самые давние наборы данных сверх MAX_DATASETS.

        День, которому считают шаги в фоне, не забывается: досчёт пишет в свою запись, и поднимать рядом
        вторую незачем. Остальные уходят в _evicted: они забыты, но пока на них кто-то ссылается, это
        по-прежнему единственный экземпляр дня.
        """
        while len(self._items) > MAX_DATASETS:
            oldest = next(
                (key for key, item in self._items.items() if not item.precomputing),
                None,
            )
            if oldest is None:
                return
            self._evicted[oldest] = self._items.pop(oldest)


def _restored(state: DayState, writer: DayWriter) -> DatasetRecord:
    """DatasetRecord из дня, поднятого хранилищем: солвер при этом не запускается ни разу."""
    record = DatasetRecord(
        dataset_id=state.dataset_id,
        status=state.status,
        stage=state.stage,
        report=state.report,
        error=state.error,
        prepared=state.prepared,
        base=state.base,
        session=state.base,
        cursor=state.cursor,
        day_revision=state.day_revision,
        last_version=state.last_version,
        agreed=dict(state.agreed),
        store=writer,
    )
    record.timeline.entries = sorted(state.entries, key=lambda entry: entry.order)
    record.timeline.steps = dict(state.steps)
    record.timeline.revision = state.revision
    record.timeline.last_number = state.last_number
    if state.base is not None:
        _restore_plan(record)
    return record


def _restore_plan(record: DatasetRecord) -> None:
    """Ставит план на сохранённое время по посчитанным шагам — тем самым, которые видел диспетчер.

    То же, что делает move_cached, только без пересчёта: шаг, который не успел попасть в базу (перезапуск
    застал солвер за работой), досчитается в фоне, и settle доведёт план до времени на часах.
    """
    count = record.timeline.applied_count(record.cursor)
    walk = record.timeline.walk(record.base, count)
    record.session = walk.session
    if walk.awaiting is not None:
        record.cursor = walk.awaiting.event.time
    elif walk.done < count:
        # Шага не хватает: перезапуск застал солвер за работой. Часы встают на время последнего посчитанного
        # шага, как move_cached делает на «ломающем» событии без выбора, иначе на экране были бы часы на 11:00
        # и план на 10:00 — с заявкой, которую диспетчер только что отменил. Досчёт вернёт часы обратно.
        record.resume_cursor = record.cursor
        record.cursor = record.timeline.entries[walk.done - 1].event.time if walk.done else 0
