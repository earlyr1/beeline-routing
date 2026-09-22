"""Чтение выгрузок Билайна: «Синтетические данные» и «Контрольное распределение»."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from app.domain.timeutil import parse_beeline_datetime

REQUIRED_COLUMNS = ("Заявка", "Тип заявки BK", "Тип заявки HD", "Начало", "Окончание", "Район", "Адрес")
OFFICE_MARKER = "адрес офиса"


@dataclass(frozen=True)
class RawRequestRow:
    row_index: int
    request_id: str
    type_bk: str
    type_hd: str
    window_start: int
    window_end: int
    district: str
    address: str
    status_bk: str = ""
    crew: str = ""
    # Колонка «Подключение»: FMC, FTTB или пусто. Непустое значение — у клиента настраивают подключение.
    connection: str = ""
    line_no: int = 0  # номер строки в файле, для отчёта о пропущенных строках


@dataclass(frozen=True)
class RawFile:
    rows: list[RawRequestRow]
    office_address: str | None
    is_control: bool
    skipped: list[str] = field(default_factory=list)
    # Замечания к окнам из данных для отчёта разбора. Разбор их не ставит: окна проверяет window_check.py,
    # которому нужны нормативы и сетка окон из конфига.
    window_warnings: list[str] = field(default_factory=list)


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Не удалось определить кодировку файла: ожидается UTF-8 или Windows-1251")


def _cell(record: dict[str, str | None], column: str) -> str:
    return (record.get(column) or "").strip()


def parse_beeline_csv(data: bytes) -> RawFile:
    reader = csv.DictReader(io.StringIO(decode_bytes(data)), delimiter=";")
    header = reader.fieldnames or []
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise ValueError(f"В файле нет колонок: {', '.join(missing)}")

    rows: list[RawRequestRow] = []
    skipped: list[str] = []
    office: str | None = None
    for line_no, record in enumerate(reader, start=2):
        request_id = _cell(record, "Заявка")
        if request_id.lower() == OFFICE_MARKER:
            office = _cell(record, "Тип заявки BK") or None
            continue
        if not any((value or "").strip() for value in record.values() if isinstance(value, str)):
            continue
        start_raw, end_raw = _cell(record, "Начало"), _cell(record, "Окончание")
        if not request_id or not start_raw or not end_raw:
            skipped.append(f"строка {line_no}: нет номера заявки или временного окна")
            continue
        try:
            window_start = parse_beeline_datetime(start_raw)
            window_end = parse_beeline_datetime(end_raw)
        except ValueError as error:
            skipped.append(f"строка {line_no}: {error}")
            continue
        rows.append(
            RawRequestRow(
                row_index=len(rows),
                request_id=request_id,
                type_bk=_cell(record, "Тип заявки BK"),
                type_hd=_cell(record, "Тип заявки HD"),
                window_start=window_start,
                window_end=window_end,
                district=_cell(record, "Район"),
                address=_cell(record, "Адрес"),
                status_bk=_cell(record, "Статус BK"),
                crew=_cell(record, "Бригада"),
                connection=_cell(record, "Подключение"),
                line_no=line_no,
            )
        )
    return RawFile(rows=rows, office_address=office, is_control="Бригада" in header, skipped=skipped)
