"""День диспетчера переживает перезапуск backend: тот же план, та же шкала, те же договорённости.

Памяти тут места нет: перезапускать нечего, если день никуда не записан. Поэтому файл переопределяет
state_backend (tests/conftest.py) на одну только базу — каждый тест идёт один раз и на Postgres, а без
TEST_DATABASE_URL пропускается целиком.
"""

import pytest

from app.domain.enums import EventType
from app.domain.models import Event
from app.planning import session as session_module
from app.state.repo import StateUnavailable
from tests.api_helpers import csv_bytes, sample_bundle, upload


@pytest.fixture
def state_backend(postgres_state):
    """День этого файла живёт только в базе: на памяти проверять нечего."""
    return postgres_state


class Background(list):
    """Фоновые задачи не запускаются сами: тест выполняет их, когда нужно."""

    def run(self):
        while self:
            self.pop(0)()


def open_day(api):
    """Клиент с днём из бандла, сохранённым в базу. Фоновый предподсчёт выполняется тут же."""
    client, deps = api()
    background = Background()
    deps.run_background = background.append
    dataset_id = upload(client, "bundle.json", sample_bundle().model_dump_json().encode())
    assert client.get(f"/api/datasets/{dataset_id}").json()["status"] == "ready"
    return client, deps, f"/api/datasets/{dataset_id}", background


def restart(api, monkeypatch):
    """Новый процесс backend: та же база, тот же kv-кэш, но решатель под запретом.

    Запрет и есть проверка: восстановленный день показывает записанный план, а не посчитанный заново.
    Солвер ограничен по времени, и второй запуск нашёл бы другие маршруты.
    """
    client, deps = api()
    deps.run_background = Background().append

    def forbidden(*args, **kwargs):
        raise AssertionError("после перезапуска план должен читаться из базы, а не считаться заново")

    monkeypatch.setattr(session_module, "_solve", forbidden)
    return client


def reopen(api):
    """Новый процесс backend, которому досчитывать шаги можно: его очередь фоновых задач отдаётся тесту."""
    client, deps = api()
    background = Background()
    deps.run_background = background.append
    return client, deps, background


def day_record(deps, base):
    return deps.registry.get(base.rsplit("/", 1)[-1])


def cancel_at(client, base, time, request_id):
    return client.post(
        f"{base}/timeline/events", json={"type": "cancel", "time": time, "request_id": request_id}
    )


def test_plan_timeline_cursor_and_calls_survive_a_restart(api, monkeypatch):
    client, _, base, background = open_day(api)
    breaking = Event(type=EventType.ENGINEER_UNAVAILABLE, time="11:00", engineer_id="E1")
    assert client.post(f"{base}/timeline/events", json=breaking.model_dump(mode="json")).status_code == 200
    background.run()
    assert client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "keep"}).status_code == 200
    background.run()
    assert client.post(f"{base}/cursor", json={"time": "12:30"}).status_code == 200
    window = {"window": {"start": "14:00", "end": "16:00", "asap": False}, "version": 2}
    assert client.put(f"{base}/agreed/R2", json=window).status_code == 200
    background.run()
    before = client.get(f"{base}/state").json()
    assert before["cursor"] == "12:30"
    assert [item["variant"] for item in before["timeline"]] == ["keep"]
    assert before["agreed"]["R2"]["window"] == {"start": "14:00", "end": "16:00", "asap": False}

    after = restart(api, monkeypatch).get(f"{base}/state").json()
    assert after == before


def test_a_day_the_service_never_saw_is_not_found(api, monkeypatch):
    open_day(api)
    assert restart(api, monkeypatch).get("/api/datasets/d_nosuch/state").status_code == 404


def test_numbering_goes_on_after_a_restart(api, monkeypatch):
    """Номера событий и планов не начинаются заново: иначе новые ключи шагов налезли бы на старые."""
    client, _, base, background = open_day(api)
    assert (
        client.post(
            f"{base}/timeline/events", json={"type": "cancel", "time": "10:00", "request_id": "R3"}
        ).status_code
        == 200
    )
    background.run()
    before = client.get(f"{base}/state").json()

    fresh = restart(api, monkeypatch)
    state = fresh.get(f"{base}/state").json()
    assert [item["id"] for item in state["timeline"]] == ["tl_1"]
    assert state["version"] == before["version"]
    # Событие после перезапуска получает следующий номер, а не первый.
    assert (
        fresh.post(
            f"{base}/timeline/events", json={"type": "cancel", "time": "10:30", "request_id": "R2"}
        ).status_code
        == 200
    )
    assert [item["id"] for item in fresh.get(f"{base}/state").json()["timeline"]] == ["tl_1", "tl_2"]


def test_a_write_the_base_refused_stays_out_of_memory_too(api):
    """Неудавшаяся запись не оставляет события на экране и не ломает следующие: они не получают 409.

    До этого правка ложилась в память раньше базы: день на экране продолжал жить «сохранённым», а каждая
    следующая правка шкалы отвечала «Данные изменились в другой вкладке».
    """
    client, deps, base, background = open_day(api)
    writer = day_record(deps, base).store
    real, refusals = writer.add_entry, {"left": 1}

    def flaky(entry, *, revision, expect):
        if refusals["left"]:
            refusals["left"] -= 1
            raise StateUnavailable("Не удалось сохранить: база недоступна.")
        return real(entry, revision=revision, expect=expect)

    writer.add_entry = flaky
    refused = cancel_at(client, base, "10:00", "R3")
    assert refused.status_code == 503
    assert client.get(f"{base}/state").json()["timeline"] == []

    assert cancel_at(client, base, "10:30", "R2").status_code == 200
    assert cancel_at(client, base, "11:00", "R1").status_code == 200
    background.run()
    shown = client.get(f"{base}/state").json()["timeline"]

    fresh, _, _ = reopen(api)
    assert fresh.get(f"{base}/state").json()["timeline"] == shown


def test_a_precompute_the_base_broke_starts_again(api):
    """Обрыв базы в фоновом счёте не замораживает шкалу: следующий /state начинает счёт заново.

    Иначе ревизия оставалась занятой упавшим счётом, timeline_ready навсегда оставался false, и вкладка
    опрашивала /state раз в секунду вечно.
    """
    client, deps, base, background = open_day(api)
    record = day_record(deps, base)
    real, broken = record.store.add_step, {"on": True}

    def flaky(key, step, *, last_version):
        if broken["on"]:
            raise StateUnavailable("Не удалось сохранить: база недоступна.")
        return real(key, step, last_version=last_version)

    record.store.add_step = flaky
    assert cancel_at(client, base, "10:00", "R3").status_code in (200, 503)
    background.run()
    assert record.precompute_revision is None and record.precomputing == 0
    assert client.get(f"{base}/state").json()["timeline_ready"] is False

    broken["on"] = False
    for _ in range(5):
        client.get(f"{base}/state")
        background.run()
    assert client.get(f"{base}/state").json()["timeline_ready"] is True


def test_a_restart_that_caught_the_solver_keeps_clock_and_plan_together(api):
    """Шаг не успел попасть в базу: часы встают на время посчитанного плана, а досчёт возвращает их назад.

    Иначе на экране были бы часы на 11:00 и план на 10:00 — с заявкой, которую диспетчер только что отменил.
    """
    client, deps, base, background = open_day(api)
    assert cancel_at(client, base, "10:00", "R3").status_code == 200
    assert cancel_at(client, base, "11:00", "R2").status_code == 200
    background.run()
    assert client.post(f"{base}/cursor", json={"time": "11:00"}).status_code == 200
    background.run()
    before = client.get(f"{base}/state").json()
    assert before["cursor"] == "11:00" and before["now"] == "11:00"

    dataset_id = base.rsplit("/", 1)[-1]
    with deps.state.cursor() as cur:
        cur.execute("DELETE FROM plans WHERE dataset_id = %s AND token LIKE 'tl_2%%'", (dataset_id,))
        # Стратегию отмены диспетчер не выбирал: в базе шаги всех трёх, и пропадают все.
        assert cur.rowcount == 3

    fresh, _, fresh_background = reopen(api)
    caught = fresh.get(f"{base}/state").json()
    assert caught["cursor"] == caught["now"] == "10:00" and caught["timeline_ready"] is False

    fresh_background.run()
    settled = fresh.get(f"{base}/state").json()
    assert settled["cursor"] == "11:00" and settled["now"] == "11:00"
    assert settled["timeline_ready"] is True


def test_everything_about_the_status_comes_back_except_the_address_counter(api, monkeypatch):
    """У поднятого дня совпадает весь статус: и стадия, и отчёт разбора, и ошибка. Кроме счётчика адресов.

    Счётчик (done/total) в базу не идёт нарочно (app/state/repo.py): он тикает на каждый адрес, а день,
    пойманный перезапуском на предподсчёте, всё равно не возобновляется — готовому же дню считать нечего.
    Расхождение одно на весь DatasetStatus, и пусть оно будет описанным, а не найденным.
    """
    client, deps = api()
    deps.run_background = Background().append
    rows = [("N1", "10:00", "12:00", "Город Москва, ул.Таганская, д. 1")]
    dataset_id = upload(client, "new.csv", csv_bytes(rows, office=None))
    before = client.get(f"/api/datasets/{dataset_id}").json()
    assert before["status"] == "ready" and before["progress"] == {"done": 1, "total": 1}

    after = restart(api, monkeypatch).get(f"/api/datasets/{dataset_id}").json()

    assert after == {**before, "progress": {"done": 0, "total": 0}}
