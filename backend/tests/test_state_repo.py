"""Контракт хранилища дня: память ничего не сохраняет, Postgres поднимает день ровно таким, каким записал.

Постгресовая часть идёт, только если задан TEST_DATABASE_URL (например,
`TEST_DATABASE_URL=postgresql://routing:routing@localhost:55432/routing`); без него она пропускается, и
обычный прогон тестов базы не требует.
"""

import os

import pytest

from app.api.registry import PreparedDay
from app.api.schemas import AgreedWindow, TimeWindow
from app.llm.schemas import Proposal
from app.planning.timeline import Timeline, TimelineStep, entry_token, replay_step
from app.state.memory import MemoryStateRepo
from app.state.repo import StateConflict
from tests.planning_helpers import OFFICE, context, day_engineers, day_requests, new_session
from tests.timeline_helpers import cancel

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
needs_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан: базы для теста нет"
)


def prepared_day():
    return PreparedDay("t", "Тест", OFFICE, day_requests(), day_engineers(), None, False)


def agreed_on(start, end, version):
    return AgreedWindow(
        window=TimeWindow(start=start, end=end),
        request_window=TimeWindow(start=start, end=end),
        version=version,
    )


def test_memory_repo_keeps_nothing():
    """Без DATABASE_URL день живёт только в памяти реестра: сериализации нет, поднимать нечего."""
    repo = MemoryStateRepo()
    writer = repo.create("d_memory")
    session = new_session()
    writer.save_day(prepared_day(), session, revision=0, day_revision=0, last_number=0, last_version=1)
    writer.save_cursor(600)
    writer.bump_number(3)
    assert repo.load("d_memory") is None
    repo.close()


@pytest.fixture
def repo():
    from app.state.migrate import migrate
    from app.state.postgres import PostgresStateRepo

    migrate(TEST_DATABASE_URL)
    store = PostgresStateRepo(TEST_DATABASE_URL, context())
    with store.cursor() as cur:
        cur.execute("DELETE FROM days")
    yield store
    store.close()


def reopen():
    """Тот же день глазами нового процесса: соединение другое, данные те же."""
    from app.state.postgres import PostgresStateRepo

    return PostgresStateRepo(TEST_DATABASE_URL, context())


def saved_day(repo, dataset_id="d_pg"):
    """День с утренним планом в базе: дальше на него вешаются события и шаги."""
    writer = repo.create(dataset_id)
    session = new_session()
    writer.save_day(prepared_day(), session, revision=0, day_revision=0, last_number=0, last_version=1)
    writer.save_status(status="ready", stage="ready", report=None, error=None)
    return writer, session


@needs_postgres
def test_unknown_day_is_not_found(repo):
    assert repo.load("d_missing") is None


@needs_postgres
def test_the_whole_day_comes_back(repo):
    writer, morning = saved_day(repo)
    timeline = Timeline()
    entry = timeline.create(cancel("R2", "09:00"))
    timeline.insert(entry)
    writer.add_entry(entry, revision=1, expect=0)
    writer.bump_number(1)
    walk = timeline.walk(morning)
    step = replay_step(morning, entry, context(), 2)
    writer.add_step((walk.prefix, entry_token(entry)), step, last_version=2)
    writer.save_cursor(600)
    writer.save_agreed("R1", agreed_on("10:00", "12:00", 2))
    writer.save_proposals(
        [
            Proposal(
                id="pr_1",
                status="pending",
                event=cancel("R3", "10:00"),
                rationale="клиент отказался",
                source_text="отмени R3",
                created_at_version=2,
            )
        ],
        urgent_number=4,
    )

    day = reopen().load("d_pg")
    assert day is not None
    # Утренний план тот же, байт в байт: его не пересчитывали, а прочитали.
    assert day.base.plan.model_dump_json() == morning.plan.model_dump_json()
    assert [item.id for item in day.entries] == ["tl_1"]
    assert day.entries[0].event.request_id == "R2"
    restored = day.steps[(walk.prefix, entry_token(entry))]
    assert restored.session.plan.model_dump_json() == step.session.plan.model_dump_json()
    assert restored.applied.event.model_dump() == step.applied.event.model_dump()
    assert (day.cursor, day.last_number, day.last_version) == (600, 1, 2)
    assert day.agreed["R1"].window.model_dump() == {"start": 600, "end": 720, "asap": False}
    assert [p.id for p in day.proposals] == ["pr_1"] and day.urgent_number == 4


@needs_postgres
def test_the_plan_the_dispatcher_saw_is_not_overwritten(repo):
    """Солвер ограничен по времени и недетерминирован: повторный расчёт того же шага затирать план не вправе."""
    writer, morning = saved_day(repo)
    timeline = Timeline()
    entry = timeline.create(cancel("R2", "09:00"))
    timeline.insert(entry)
    writer.add_entry(entry, revision=1, expect=0)
    key = (timeline.walk(morning).prefix, entry_token(entry))
    shown = replay_step(morning, entry, context(), 2)
    writer.add_step(key, shown, last_version=2)
    # Тот же ключ, другой план: так выглядел бы второй запуск солвера на той же задаче.
    other = replay_step(morning, entry, context(), 2)
    writer.add_step(key, TimelineStep(session=morning, applied=other.applied), last_version=2)

    day = reopen().load("d_pg")
    assert day.steps[key].session.plan.model_dump_json() == shown.session.plan.model_dump_json()


@needs_postgres
def test_rejected_step_keeps_the_previous_plan(repo):
    """У отклонённого шага своего плана нет: на подъёме он берёт план предыдущего шага."""
    writer, morning = saved_day(repo)
    timeline = Timeline()
    first = timeline.create(cancel("R2", "09:00"))
    timeline.insert(first)
    writer.add_entry(first, revision=1, expect=0)
    walk = timeline.walk(morning)
    step = replay_step(morning, first, context(), 2)
    timeline.store(walk, first, step)
    writer.add_step((walk.prefix, entry_token(first)), step, last_version=2)
    # Второе событие отклонено: заявку уже отменили.
    second = timeline.create(cancel("R2", "10:00"))
    timeline.insert(second)
    writer.add_entry(second, revision=2, expect=1)
    walk = timeline.walk(morning)
    rejected = replay_step(walk.session, second, context(), 3)
    assert rejected.reason is not None
    writer.add_step((walk.prefix, entry_token(second)), rejected, last_version=2)

    day = reopen().load("d_pg")
    restored = day.steps[(walk.prefix, entry_token(second))]
    assert restored.reason == rejected.reason
    assert restored.session.plan.model_dump_json() == step.session.plan.model_dump_json()


@needs_postgres
def test_pruning_never_drops_the_morning_plan(repo):
    writer, morning = saved_day(repo)
    timeline = Timeline()
    entry = timeline.create(cancel("R2", "09:00"))
    timeline.insert(entry)
    writer.add_entry(entry, revision=1, expect=0)
    walk = timeline.walk(morning)
    writer.add_step(
        (walk.prefix, entry_token(entry)), replay_step(morning, entry, context(), 2), last_version=2
    )
    writer.keep_steps([])

    day = reopen().load("d_pg")
    assert day.base is not None and day.steps == {}


@needs_postgres
def test_day_built_anew_forgets_events_and_calls(repo):
    writer, morning = saved_day(repo)
    timeline = Timeline()
    entry = timeline.create(cancel("R2", "09:00"))
    timeline.insert(entry)
    writer.add_entry(entry, revision=1, expect=0)
    writer.save_agreed("R1", agreed_on("10:00", "12:00", 1))
    # Пересборка дня: номера событий не начинаются заново, всё остальное — начинается.
    writer.save_day(prepared_day(), morning, revision=2, day_revision=2, last_number=1, last_version=5)

    day = reopen().load("d_pg")
    assert day.entries == [] and day.steps == {} and day.agreed == {}
    assert (day.cursor, day.last_number, day.last_version) == (0, 1, 5)
    assert day.revision == day.day_revision == 2


@needs_postgres
def test_a_stranger_writing_the_same_day_is_refused(repo):
    """Оптимистичная проверка ревизии: при одном процессе не срабатывает, при двух даёт внятный отказ."""
    writer, _ = saved_day(repo)
    timeline = Timeline()
    entry = timeline.create(cancel("R2", "09:00"))
    with pytest.raises(StateConflict):
        writer.add_entry(entry, revision=9, expect=8)


@needs_postgres
def test_preprocessing_interrupted_by_a_restart_is_not_a_spinner(repo):
    repo.create("d_busy")
    day = reopen().load("d_busy")
    assert day.status == "failed" and "перезапуск" in day.error


@needs_postgres
def test_an_orphan_continuation_does_not_pass_for_a_step_of_the_new_plan(repo):
    """Продолжение шага, которого в базе не оказалось, не выдаёт себя за продолжение пересчитанного.

    Решатель недетерминирован: пересчитанный шаг — другой план, и цепочка от прежнего к нему не относится.
    """
    writer, morning = saved_day(repo)
    timeline = Timeline()
    first = timeline.create(cancel("R2", "09:00"))
    timeline.insert(first)
    writer.add_entry(first, revision=1, expect=0)
    walk = timeline.walk(morning)
    step = replay_step(morning, first, context(), 2)
    timeline.store(walk, first, step)
    key = (walk.prefix, entry_token(first))
    second = timeline.create(cancel("R3", "10:00"))
    timeline.insert(second)
    writer.add_entry(second, revision=2, expect=1)
    child = timeline.walk(morning, 2)
    # Шаг первого события в базу не попал, а продолжение попало: так выглядит база после сбоя записи.
    writer.add_step(
        ((*key[0], key[1]), entry_token(second)),
        replay_step(step.session, second, context(), 3),
        last_version=3,
    )
    assert child.done == 1

    writer.add_step(key, step, last_version=2)

    day = reopen().load("d_pg")
    assert list(day.steps) == [key], "сирота осталась в базе и выдала себя за продолжение"


@needs_postgres
def test_a_day_written_by_another_build_reads_as_missing(repo):
    """День, снятый прежними моделями, не валит запросы в 500: он ведёт себя как ненайденный."""
    repo.create("d_old")
    with repo.cursor() as cur:
        cur.execute(
            "UPDATE days SET prepared = %s::jsonb WHERE dataset_id = 'd_old'",
            ('{"region": "t", "region_title": "Тест"}',),
        )
    assert repo.load("d_old") is None
