"""День диспетчера в Postgres: psycopg 3 и обычный SQL, без ORM.

Пять таблиц (миграция backend/migrations/0001.day-state.sql):

- days              — день: входные данные, отчёт разбора, текущее время, счётчики номеров;
- timeline_entries  — события шкалы в порядке применения;
- plans             — кэш шагов: готовый план после события. Утренний план — строка с пустым ключом;
- agreed_windows    — согласованные окна вкладки «Коммуникации»;
- proposals         — предложения помощника со своими статусами.

Матрица дороги в базу не едет: это чистая функция от точек дня, и на подъёме её собирает тот же make_problem
из kv-кэша. Пул солвера, блокировки и прогресс предподсчёта тоже не хранятся — это состояние процесса.

Каждая логическая правка — одна транзакция: падение посреди неё не оставляет день в середине. У планов
«первый писатель выигрывает» (ON CONFLICT DO NOTHING): солвер ограничен по времени и недетерминирован,
поэтому повторный расчёт того же ключа не имеет права затереть план, который диспетчер уже видел.

База рассчитана РОВНО НА ОДИН процесс backend (один воркер uvicorn — так он и запускается в Dockerfile).
Ревизия шкалы ловит вторую реплику только на событиях; курсор, согласованные окна, предложения и планы
ей не защищены, а _fail_interrupted гасит все считающиеся дни, не разбирая, чей это день. Нужны две
реплики — состояние дня придётся переносить в базу целиком, с блокировкой на день.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool, PoolTimeout
from pydantic import ValidationError

from app.api.schemas import AgreedWindow, TimeWindow, UploadReport
from app.llm.schemas import Proposal
from app.planning.models import EventVariant
from app.planning.session import PlanningContext, PlanningSession
from app.planning.timeline import StepKey, TimelineEntry, TimelineStep
from app.state.codec import (
    MatrixCache,
    PreparedDay,
    clean_text,
    dump_applied,
    dump_event,
    dump_geo,
    dump_model,
    dump_prepared,
    dump_session,
    entry_from_row,
    load_prepared,
    load_session,
    step_from_row,
)
from app.state.repo import (
    DayState,
    DayWriter,
    StateBroken,
    StateConflict,
    StateMissing,
    StateUnavailable,
)

logger = logging.getLogger(__name__)

# Сколько последних дней держит база. Лишние уносит создание нового дня, вместе со шкалой, планами,
# окнами и предложениями (ON DELETE CASCADE). День, который процесс ещё держит в памяти, из этого счёта
# исключается (аргумент keep у create): писать в вытесненный день было бы некуда.
MAX_DAYS = 20
SAVE_FAILED_TEXT = "Не удалось сохранить: база недоступна."
# База ответила, но правку не приняла: битые данные, нарушенное ограничение, опечатка в SQL. Повтор запроса
# не поможет, и врать про недоступность на живой базе незачем.
SAVE_BROKEN_TEXT = "Не удалось сохранить: база не приняла данные дня."
# Дня в базе уже нет: его вытеснили новые загрузки, пока процесс держал его в памяти (см. MAX_DAYS).
MISSING_TEXT = "День больше не хранится в базе: загрузите файл заново."
CONFLICT_TEXT = "Данные изменились в другой вкладке, обновите страницу."
# День, пойманный перезапуском на предподсчёте, не возобновляется: честный экран вместо вечного спиннера.
INTERRUPTED_TEXT = "Предподсчёт прервал перезапуск сервиса: загрузите файл заново."


def _window(window: TimeWindow | None) -> str | None:
    return None if window is None else dump_model(window)


def _parse_window(raw: str | None) -> TimeWindow | None:
    return None if raw is None else TimeWindow.model_validate_json(raw)


class PostgresDayWriter:
    """Ручка одного дня: все методы — одна правка и одна транзакция."""

    def __init__(self, repo: PostgresStateRepo, dataset_id: str) -> None:
        self._repo = repo
        self._id = dataset_id

    def save_status(self, *, status: str, stage: str, report: UploadReport | None, error: str | None) -> None:
        with self._repo.cursor() as cur:
            cur.execute(
                "UPDATE days SET status = %s, stage = %s, report = %s::jsonb, error = %s, "
                "updated_at = now() WHERE dataset_id = %s",
                (status, stage, None if report is None else dump_model(report), clean_text(error), self._id),
            )
            _found_day(cur, self._id)

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
        payload = dump_prepared(prepared)
        morning = dump_session(session)
        with self._repo.cursor() as cur:
            cur.execute(
                "UPDATE days SET prepared = %s::jsonb, cursor_min = 0, revision = %s, day_revision = %s, "
                "last_number = %s, last_version = %s, updated_at = now() WHERE dataset_id = %s",
                (payload, revision, day_revision, last_number, last_version, self._id),
            )
            _found_day(cur, self._id)
            # День собран заново: прежние события, планы и договорённости к нему не относятся.
            cur.execute("DELETE FROM timeline_entries WHERE dataset_id = %s", (self._id,))
            cur.execute("DELETE FROM plans WHERE dataset_id = %s", (self._id,))
            cur.execute("DELETE FROM agreed_windows WHERE dataset_id = %s", (self._id,))
            cur.execute(
                "INSERT INTO plans (dataset_id, prefix, token, session) VALUES (%s, '{}', '', %s::jsonb)",
                (self._id, morning),
            )

    def save_cursor(self, cursor: int) -> None:
        with self._repo.cursor() as cur:
            cur.execute(
                "UPDATE days SET cursor_min = %s, updated_at = now() WHERE dataset_id = %s",
                (cursor, self._id),
            )
            _found_day(cur, self._id)

    def add_entry(self, entry: TimelineEntry, *, revision: int, expect: int) -> None:
        with self._repo.cursor() as cur:
            cur.execute(
                "INSERT INTO timeline_entries "
                "(dataset_id, entry_id, seq, time_min, event, geo, checked, variant) "
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)",
                (
                    self._id,
                    entry.id,
                    entry.seq,
                    entry.event.time,
                    dump_event(entry.event),
                    dump_geo(entry.geo),
                    entry.checked,
                    entry.variant,
                ),
            )
            _bump_revision(cur, self._id, revision, expect)

    def drop_entry(self, entry_id: str, keys: Collection[StepKey], *, revision: int, expect: int) -> None:
        with self._repo.cursor() as cur:
            cur.execute(
                "DELETE FROM timeline_entries WHERE dataset_id = %s AND entry_id = %s", (self._id, entry_id)
            )
            _keep_steps(cur, self._id, keys)
            _bump_revision(cur, self._id, revision, expect)

    def set_variant(self, entry_id: str, variant: EventVariant, *, revision: int, expect: int) -> None:
        with self._repo.cursor() as cur:
            cur.execute(
                "UPDATE timeline_entries SET variant = %s WHERE dataset_id = %s AND entry_id = %s",
                (variant, self._id, entry_id),
            )
            _bump_revision(cur, self._id, revision, expect)

    def bump_number(self, last_number: int) -> None:
        with self._repo.cursor() as cur:
            cur.execute(
                "UPDATE days SET last_number = greatest(last_number, %s), updated_at = now() "
                "WHERE dataset_id = %s",
                (last_number, self._id),
            )
            _found_day(cur, self._id)

    def add_step(self, key: StepKey, step: TimelineStep, *, last_version: int) -> None:
        prefix, token = key
        # У отклонённого шага плана нет: план остаётся прежним, и на подъёме он берётся у предыдущего шага.
        session = None if step.applied is None and step.reason is not None else dump_session(step.session)
        with self._repo.cursor() as cur:
            cur.execute(
                "INSERT INTO plans (dataset_id, prefix, token, applied, session, reason) "
                "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s) ON CONFLICT DO NOTHING",
                (self._id, list(prefix), token, dump_applied(step.applied), session, clean_text(step.reason)),
            )
            if cur.rowcount:
                # Шага в базе не было, а продолжения от него могли остаться: так бывает, если его запись
                # когда-то не удалась, а следующий шаг посчитался от плана в памяти и записался. Решатель
                # недетерминирован, поэтому теперь тут другой план, и старые продолжения к нему не относятся:
                # выдать себя за продолжение они не должны. Строки, вставленной только что, срез не заденет —
                # у неё префикс короче.
                child = [*prefix, token]
                cur.execute(
                    "DELETE FROM plans WHERE dataset_id = %s AND prefix[1:%s] = %s::text[]",
                    (self._id, len(child), child),
                )
            cur.execute(
                "UPDATE days SET last_version = greatest(last_version, %s), updated_at = now() "
                "WHERE dataset_id = %s",
                (last_version, self._id),
            )
            _found_day(cur, self._id)

    def keep_steps(self, keys: Collection[StepKey]) -> None:
        with self._repo.cursor() as cur:
            _keep_steps(cur, self._id, keys)

    def save_agreed(self, request_id: str, window: AgreedWindow) -> None:
        with self._repo.cursor() as cur:
            cur.execute(
                "INSERT INTO agreed_windows "
                "(dataset_id, request_id, client_window, request_window, version) "
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s) "
                "ON CONFLICT (dataset_id, request_id) DO UPDATE SET "
                "client_window = excluded.client_window, request_window = excluded.request_window, "
                "version = excluded.version, agreed_at = now()",
                (
                    self._id,
                    request_id,
                    _window(window.window),
                    _window(window.request_window),
                    window.version,
                ),
            )

    def drop_agreed(self, request_id: str) -> None:
        with self._repo.cursor() as cur:
            cur.execute(
                "DELETE FROM agreed_windows WHERE dataset_id = %s AND request_id = %s",
                (self._id, request_id),
            )

    def save_proposals(self, proposals: Collection[Proposal], *, urgent_number: int) -> None:
        rows = [
            (self._id, proposal.id, seq, proposal.status, dump_model(proposal))
            for seq, proposal in enumerate(proposals)
        ]
        with self._repo.cursor() as cur:
            cur.execute("DELETE FROM proposals WHERE dataset_id = %s", (self._id,))
            if rows:
                cur.executemany(
                    "INSERT INTO proposals (dataset_id, proposal_id, seq, status, payload) "
                    "VALUES (%s, %s, %s, %s, %s::jsonb)",
                    rows,
                )
            cur.execute(
                "UPDATE days SET urgent_number = greatest(urgent_number, %s), updated_at = now() "
                "WHERE dataset_id = %s",
                (urgent_number, self._id),
            )
            _found_day(cur, self._id)


def _found_day(cur: psycopg.Cursor, dataset_id: str) -> None:
    """Правка не нашла своего дня: его вытеснили из базы новые загрузки.

    UPDATE по несуществующему дню задевает ноль строк и молча возвращается успехом, так что день, вынесенный
    вытеснением, принимал бы половину правок в никуда и отказывал по второй половине с чужой причиной
    (нарушенный внешний ключ, «данные изменились в другой вкладке»). Лучше один честный отказ на всё.
    """
    if cur.rowcount == 0:
        raise StateMissing(MISSING_TEXT)


def _bump_revision(cur: psycopg.Cursor, dataset_id: str, revision: int, expect: int) -> None:
    """Оптимистичная проверка: шкалу с тех пор никто извне процесса не менял.

    При одном процессе uvicorn не срабатывает никогда, но превращает будущую ошибку конфигурации (две реплики
    backend на одной базе) из тихой порчи состояния во внятный отказ.
    """
    cur.execute(
        "UPDATE days SET revision = %s, updated_at = now() WHERE dataset_id = %s AND revision = %s",
        (revision, dataset_id, expect),
    )
    if cur.rowcount == 0:
        # Ноль строк — либо ревизия ушла вперёд, либо дня в базе уже нет: причины разные, и путать их нельзя.
        cur.execute("SELECT 1 FROM days WHERE dataset_id = %s", (dataset_id,))
        if cur.fetchone() is None:
            raise StateMissing(MISSING_TEXT)
        raise StateConflict(CONFLICT_TEXT)


def _keep_steps(cur: psycopg.Cursor, dataset_id: str, keys: Collection[StepKey]) -> None:
    """Оставляет только перечисленные шаги. Утренний план (пустой ключ) не трогается никогда."""
    rows = list(keys)
    if not rows:
        cur.execute("DELETE FROM plans WHERE dataset_id = %s AND token <> ''", (dataset_id,))
        return
    query = sql.SQL(
        "DELETE FROM plans WHERE dataset_id = %s AND token <> '' AND (prefix, token) NOT IN ({keys})"
    ).format(keys=sql.SQL(", ").join(sql.SQL("(%s::text[], %s)") for _ in rows))
    params: list[object] = [dataset_id]
    for prefix, token in rows:
        params += [list(prefix), token]
    cur.execute(query, params)


class PostgresStateRepo:
    """Хранилище дней в Postgres. Соединение берётся из пула на операцию: фоновый предподсчёт не делит его
    с запросом."""

    def __init__(self, dsn: str, ctx: PlanningContext, *, pool_size: int = 8) -> None:
        self._ctx = ctx
        # check на выдаче — один SELECT 1: соединение, протухшее после `docker compose restart postgres`
        # или сна ноутбука, пул заменяет сам, вместо того чтобы отдать его первой же записи и получить
        # «Не удалось сохранить: база недоступна» на здоровой базе.
        self._pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=pool_size,
            open=True,
            timeout=10,
            check=ConnectionPool.check_connection,
        )
        self._pool.wait(timeout=30)
        self._fail_interrupted()

    def _fail_interrupted(self) -> None:
        """Дни, которые считались в момент перезапуска, не возобновляются: спиннер вечным не оставляем.

        Чей день — не разбирается: сервис рассчитан на один процесс (см. докстринг модуля), и второй,
        стартовавший на той же базе, погасит день первого.
        """
        with self.cursor() as cur:
            cur.execute(
                "UPDATE days SET status = 'failed', error = %s, updated_at = now() WHERE status = 'processing'",
                (INTERRUPTED_TEXT,),
            )
            if cur.rowcount:
                logger.info("Дней, прерванных перезапуском: %d — их нужно загрузить заново", cur.rowcount)

    @contextmanager
    def cursor(self) -> Iterator[psycopg.Cursor]:
        """Курсор в своей транзакции: выход без ошибки её фиксирует, ошибка откатывает.

        Отказ базы переводится в три разные причины, потому что диспетчеру они говорят разное: связи нет
        (повторить), дня нет (загрузить файл заново), данные не приняты (звать нас). Прежде всё это было
        одним «база недоступна» — в том числе на совершенно здоровой базе.
        """
        try:
            with self._pool.connection() as conn, conn.cursor() as cur:
                yield cur
        except (psycopg.OperationalError, PoolTimeout) as error:
            logger.warning("База недоступна: %s", error)
            raise StateUnavailable(SAVE_FAILED_TEXT) from error
        except psycopg.errors.ForeignKeyViolation as error:
            # Единственный внешний ключ схемы — dataset_id → days: строку писали дню, которого уже нет.
            logger.warning("Дня, которому шла правка, в базе больше нет: %s", error)
            raise StateMissing(MISSING_TEXT) from error
        except psycopg.Error as error:
            logger.exception("База не приняла правку дня: %s", error)
            raise StateBroken(SAVE_BROKEN_TEXT) from error

    def create(self, dataset_id: str, *, keep: Collection[str] = ()) -> DayWriter:
        """Новый день; самые давние сверх MAX_DAYS уносит он же.

        keep — дни, которые процесс держит в памяти: их вытеснять нельзя, иначе писать стало бы некуда.
        Порядок вытеснения задан до последнего поля: у дней, созданных в одну миллисекунду, created_at
        совпадает, и без второго ключа база выбирала бы жертву сама.
        """
        with self.cursor() as cur:
            cur.execute("INSERT INTO days (dataset_id) VALUES (%s)", (dataset_id,))
            cur.execute(
                "DELETE FROM days WHERE dataset_id <> ALL(%s) AND dataset_id IN "
                "(SELECT dataset_id FROM days ORDER BY created_at DESC, dataset_id DESC OFFSET %s)",
                (list(keep), MAX_DAYS),
            )
        return PostgresDayWriter(self, dataset_id)

    def writer(self, dataset_id: str) -> DayWriter:
        return PostgresDayWriter(self, dataset_id)

    def load(self, dataset_id: str) -> DayState | None:
        """День из базы или None, если его там нет либо он записан другой сборкой backend.

        Снимок дня — это модели этой сборки в jsonb, а том pg-data переживает `docker compose down` и
        пересборку образа. День, снятый прежними моделями, не разбирается: вместо 500 на каждый запрос
        он ведёт себя как ненайденный, и диспетчер получает привычное «Прежний план недоступен».
        """
        try:
            return self._load(dataset_id)
        except (ValidationError, ValueError) as error:
            logger.warning(
                "День %s в базе записан в другом виде и не разбирается (%s): считаем, что его нет — "
                "файл нужно загрузить заново",
                dataset_id,
                error,
            )
            return None

    def _load(self, dataset_id: str) -> DayState | None:
        with self.cursor() as cur:
            cur.execute(
                "SELECT status, stage, error, report::text, prepared::text, cursor_min, revision, "
                "day_revision, last_number, last_version, urgent_number FROM days WHERE dataset_id = %s",
                (dataset_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            state = DayState(
                dataset_id=dataset_id,
                status=row[0],
                stage=row[1],
                error=row[2],
                report=None if row[3] is None else UploadReport.model_validate_json(row[3]),
                prepared=None if row[4] is None else load_prepared(row[4]),
                cursor=row[5],
                revision=row[6],
                day_revision=row[7],
                last_number=row[8],
                last_version=row[9],
                urgent_number=row[10],
            )
            cur.execute(
                "SELECT entry_id, seq, event::text, geo::text, checked, variant "
                "FROM timeline_entries WHERE dataset_id = %s ORDER BY time_min, seq",
                (dataset_id,),
            )
            state.entries = [entry_from_row(*item) for item in cur.fetchall()]
            if state.prepared is not None:
                cur.execute(
                    "SELECT prefix, token, applied::text, session::text, reason FROM plans "
                    "WHERE dataset_id = %s ORDER BY cardinality(prefix), token",
                    (dataset_id,),
                )
                _restore_plans(state, cur.fetchall(), self._ctx)
            cur.execute(
                "SELECT request_id, client_window::text, request_window::text, version "
                "FROM agreed_windows WHERE dataset_id = %s",
                (dataset_id,),
            )
            state.agreed = {
                request_id: AgreedWindow(
                    window=_parse_window(client),
                    request_window=_parse_window(request_window),
                    version=version,
                )
                for request_id, client, request_window, version in cur.fetchall()
            }
            cur.execute(
                "SELECT payload::text FROM proposals WHERE dataset_id = %s ORDER BY seq", (dataset_id,)
            )
            state.proposals = [Proposal.model_validate_json(payload) for (payload,) in cur.fetchall()]
        return state

    def close(self) -> None:
        self._pool.close()


def _restore_plans(state: DayState, rows: Sequence[tuple], ctx: PlanningContext) -> None:
    """Утренний план и кэш шагов из строк plans: солвер при этом не запускается ни разу.

    Строки идут от коротких ключей к длинным, поэтому у отклонённого шага (своего плана у него нет) план
    предыдущего шага уже разобран. Шаг, у которого предыдущего не оказалось, пропускается: его досчитают.
    Матрицы дороги собираются один раз на набор точек — у шагов одного дня он почти всегда общий.
    """
    matrices = MatrixCache()

    def hydrate(raw: str) -> PlanningSession:
        return load_session(
            raw,
            dataset_id=state.dataset_id,
            prepared=state.prepared,
            ctx=ctx,
            matrices=matrices,
        )

    by_prefix: dict[tuple[str, ...], PlanningSession] = {}
    for prefix, token, applied, raw_session, reason in rows:
        key = (tuple(prefix), token)
        if token == "":
            state.base = hydrate(raw_session)
            by_prefix[()] = state.base
            continue
        if raw_session is not None:
            session = hydrate(raw_session)
        else:
            session = by_prefix.get(key[0])
            if session is None:
                continue
        state.steps[key] = step_from_row(session, applied, reason)
        if applied is not None:
            by_prefix[(*key[0], token)] = session
