"""Где живёт день диспетчера, пока идёт тест: в памяти процесса или в настоящем Postgres.

У хранилища дня две реализации (app/state): MemoryStateRepo не сохраняет ничего, PostgresStateRepo кладёт день
в базу. Разойтись они не имеют права, поэтому тесты уровня API гоняются на обеих: фикстура `api` строит клиента
дважды с одним и тем же телом теста, и в имени видно, чем именно он шёл — test_x[memory] и test_x[postgres].

Постгресовая половина идёт, только если задан TEST_DATABASE_URL (его подставляет `make test-db`). Без него она
пропускается, и обычный `make test` базы не требует: в конце прогона видно «N skipped», и это ровно половина
параметризованных тестов — по одному пропуску на каждый [postgres].

Солверные, симуляционные, разборные и геометрические тесты сюда не входят и базы не касаются: параметризовать
их значило бы удвоить самую долгую часть прогона ради кода, которого они не трогают.

Половина [postgres] проверяет ЗАПИСЬ: каждая правка теста уходит в базу по-настоящему. Чтение проверяется
там, где в конце теста стоит помощник `restarted` — он поднимает тот же день вторым клиентом и сверяет
ответы. Половин две сотни, и переподъём в каждой стоил бы минут, поэтому помощник висит на десятке самых
содержательных: шкала с посчитанными шагами и выбранной стратегией, согласованные окна, предложения
помощника, изменённая заявка, окно «как можно скорее».

Дёшево это стоит вот почему: база на весь прогон одна (контейнер поднимает Makefile), схему мигрируем один раз,
а тесты отделяет друг от друга `DELETE FROM days` — остальные четыре таблицы уносит ON DELETE CASCADE. На днях
в несколько строк это доли миллисекунды: TRUNCATE тех же таблиц меряется в разы дороже, а своя база или своя
схема на тест добавили бы к прогону минуты. Откат транзакции не подошёл бы вовсе: PostgresStateRepo берёт
соединения из своего пула и коммитит каждую правку сам, так что общей транзакции, которую тест мог бы
откатить, просто нет.
"""

import os

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

MEMORY = "memory"
POSTGRES = "postgres"


@pytest.fixture(scope="session")
def postgres_url():
    """Адрес тестовой базы на весь прогон; схема приводится в порядок один раз.

    Без TEST_DATABASE_URL — пропуск: базы нет, и проверять на ней нечего.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL не задан: базы для теста нет")
    from app.state.migrate import migrate

    migrate(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def _clean_days(postgres_url):
    """Чистка дней между тестами одним соединением на весь прогон: открывать своё дороже самой чистки."""
    import psycopg

    connection = psycopg.connect(postgres_url, autocommit=True)
    yield lambda: connection.execute("DELETE FROM days")
    connection.close()


@pytest.fixture
def postgres_state(postgres_url, _clean_days):
    """День этого теста живёт в Postgres. База досталась от предыдущего теста, поэтому чистим её на входе."""
    _clean_days()
    return postgres_url


@pytest.fixture(params=[MEMORY, POSTGRES])
def state_backend(request):
    """Хранилище дня для теста: None — память процесса, строка — DATABASE_URL тестовой базы.

    Файлу, которому база нужна всегда, достаточно переопределить эту фикстуру у себя одной строкой
    (см. tests/test_state_api.py): тест тогда идёт один раз и только на Postgres.
    """
    if request.param == MEMORY:
        return None
    return request.getfixturevalue("postgres_state")


@pytest.fixture
def api(tmp_path, state_backend):
    """Фабрика клиента API поверх make_client: тело теста одно, хранилища два.

    Отдаёт то же, что make_client — пару (client, deps), — и берёт те же аргументы; каталог данных у всех
    клиентов теста общий, поэтому «перезапуск сервиса» — это просто второй вызов фабрики.

    Соединения закрываются в конце теста: пулы двух сотен клиентов, оставленные открытыми, упёрлись бы
    в лимит соединений Postgres задолго до конца прогона.
    """
    from tests.api_helpers import make_client

    built = []

    def factory(*args, **options):
        client, deps = make_client(tmp_path, *args, database_url=state_backend, **options)
        built.append(deps)
        return client, deps

    yield factory
    for deps in built:
        deps.close()


@pytest.fixture
def restarted(api, state_backend):
    """Тот же день глазами нового процесса: второй клиент той же фабрики на той же базе.

    Смысл параметризации — поймать расхождение памяти и базы, а живёт оно в ЧТЕНИИ: jsonb переставляет
    ключи объектов, числа и даты возвращаются не тем же типом, поле, которое забыли записать, пропадает.
    Одна только запись этого не видит: до подъёма дня из базы дело в тесте обычно не доходит — запись
    реестра из памяти никуда не девается, и store.load никто не зовёт.

    Вешать переподъём на все параметризованные тесты незачем — прогон того не стоит; помощник ставится
    в конец самых содержательных, одной строкой: `restarted(client, f"{base}/state")`.

    На памяти перезапускать нечего: половина [memory] проходит мимо и возвращает None.
    """

    def check(client, *paths):
        if state_backend is None:
            return None
        before = {path: client.get(path).json() for path in paths}
        fresh, deps = api()
        # Досчитывать шаги новому процессу не даём: сверяем то, что поднялось из базы, а не то, что он
        # успел посчитать заново, — иначе сверка зависела бы от того, кто первым добежал.
        deps.run_background = lambda task: None
        for path, expected in before.items():
            response = fresh.get(path)
            assert response.status_code == 200, response.text
            assert response.json() == expected, f"после перезапуска {path} отвечает иначе"
        return fresh

    return check


def pytest_collection_modifyitems(items):
    """Помечает маркером `db` всё, что идёт на настоящей базе: и половины [postgres], и тесты хранилища.

    По этому маркеру `make test-db` отбирает свой прогон одним `-m db`, не перечисляя файлы руками и не
    таща в него солверные тесты тех же файлов. Половины [memory] маркера не получают: их гоняет `make test`.
    """
    for item in items:
        callspec = getattr(item, "callspec", None)
        parametrised = callspec.params.get("state_backend") if callspec is not None else None
        # postgres_url в списке фикстур — тест потребовал базу напрямую (tests/test_state_repo.py и
        # tests/test_state_api.py); POSTGRES в параметрах — это половина параметризованного теста API.
        # Спрашиваем именно postgres_url, а не postgres_state: postgres_state сам зависит от postgres_url,
        # так что замыкание фикстур содержит оба имени, а тест, взявший один только адрес базы, маркер
        # всё равно получает — иначе он не шёл бы нигде: ни в `make test`, ни в `make test-db -m db`.
        # У половины [postgres] в замыкании нет ни того, ни другого: postgres_state она берёт через
        # request.getfixturevalue, поэтому ветка по параметру нужна по-прежнему.
        if "postgres_url" in item.fixturenames or parametrised == POSTGRES:
            item.add_marker(pytest.mark.db)
