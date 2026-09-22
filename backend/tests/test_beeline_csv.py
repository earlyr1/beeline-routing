import pytest

from app.ingest.beeline_csv import parse_beeline_csv

SYNTHETIC = (
    "Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Подключение;Гигабитное подключение\r\n"
    "74198;Подключение;Конвергенция абонента;17.08.2026 20:00;17.08.2026 22:00;Кузьминки;"
    "Город Москва, пр-кт.Волгоградский, д. 128 к 5;FMC;Нет\r\n"
    "50104;Глобальная проблема;Авария;17.08.2026 0:01;17.08.2026 23:59;Кашира;Кашира, ул.Победы, д. 9;;Нет\r\n"
    ";;;;;;;;\r\n"
    "99999;Локальная заявка;Нет линка;;;Таганский;Город Москва, пер.Маяковского, д. 2;;Нет\r\n"
    "Адрес Офиса;г. Москва, ул Юных Ленинцев, д 83с 4;;;;;;;\r\n"
)

CONTROL = (
    "Заявка;Тип заявки BK;Статус BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Бригада;Подключение;"
    "Гигабитное подключение\r\n"
    "305927238;Подключение;Отменена;Конвергенция абонента;17.08.2026 20:00;17.08.2026 22:00;Кузьминки;"
    "Город Москва, пр-кт.Волгоградский, д. 128 к 5, кв. 1;Бригада Матвеев;FTTB;Нет\r\n"
)

WITHOUT_CONNECTION = (
    "Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес\r\n"
    "74198;Подключение;Заявка на подключение;17.08.2026 10:00;17.08.2026 12:00;Кузьминки;"
    "Город Москва, ул.Тестовая, д. 1\r\n"
)


def test_parses_cp1251_synthetic_file_and_finds_office():
    raw = parse_beeline_csv(SYNTHETIC.encode("cp1251"))
    assert not raw.is_control
    assert raw.office_address == "г. Москва, ул Юных Ленинцев, д 83с 4"
    assert [row.request_id for row in raw.rows] == ["74198", "50104"]
    first, second = raw.rows
    assert (first.window_start, first.window_end) == (1200, 1320)
    assert (second.window_start, second.window_end) == (1, 1439)
    assert second.row_index == 1
    assert (first.line_no, second.line_no) == (2, 3)
    assert (first.connection, second.connection) == ("FMC", "")
    assert raw.skipped == ["строка 5: нет номера заявки или временного окна"]


def test_parses_utf8_control_file_with_crew_and_status():
    raw = parse_beeline_csv(CONTROL.encode("utf-8"))
    assert raw.is_control
    row = raw.rows[0]
    assert (row.crew, row.status_bk, row.connection) == ("Бригада Матвеев", "Отменена", "FTTB")


def test_file_without_connection_column_leaves_it_empty():
    raw = parse_beeline_csv(WITHOUT_CONNECTION.encode("utf-8"))
    assert raw.rows[0].connection == ""


def test_rejects_file_without_required_columns():
    with pytest.raises(ValueError, match="нет колонок"):
        parse_beeline_csv(b"a;b\r\n1;2\r\n")


def test_a_zero_byte_in_a_cell_is_thrown_away():
    """\x00 в ячейке — мусор кодировки, а Postgres такого текста не держит: день с ним не сохранился бы."""
    raw = parse_beeline_csv(WITHOUT_CONNECTION.replace("ул.Тестовая", "ул.\x00Тестовая").encode("cp1251"))

    assert raw.rows[0].address == "Город Москва, ул.Тестовая, д. 1"
