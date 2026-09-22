"""Проверка временных окон, пришедших ИЗ ДАННЫХ: показываем, но не подменяем.

Окно, которое ВЫБРАЛ диспетчер, сервер кладёт на сетку окон (app/domain/windows.py) и мимо сетки не пускает.
Окно из выгрузки — другое дело: файл и есть источник правды, а окно аварии на весь день (00:01–23:59) должно
работать как есть. Поэтому подмен здесь нет ни одной. Строка либо выпадает целиком — работать в её окне нельзя
вообще, и заявка с таким окном только испортила бы план из-за ошибки в данных, — либо остаётся в дне вместе
с замечанием, которое диспетчер видит в отчёте разбора сразу после загрузки.

Что проверяется у каждой строки выгрузки:

* конец окна не позже начала — строка пропускается, как и строка с неразобранным временем (beeline_csv.py);
* окно за горизонтом планирования — строка пропускается: работать в нём нельзя, а расчёт оно роняет целиком;
* окно целиком вне рабочего дня смен — заявка остаётся, но приехать в неё некому;
* окно приезда короче работ своего типа — остаётся, и причина неназначения называет те же числа
  (app/solvers/reasons.py);
* окно не совпало со слотом сетки — остаётся; сколько таких окон и примеры, отчёт говорит одной строкой.

Окно приезда — это не время работ: клиенту обещано, что бригада ПРИЕДЕТ в окно, а работы спокойно заканчиваются
позже (app/solvers/simulate.py считает опоздание по началу визита). Поэтому про короткое окно сказано именно то,
что в нём не так: приехать надо ровно в эти минуты.

Окном 00:01–23:59 выгрузка отмечает аварию «весь день» — это пометка данных, а не ошибка, и замечанием она не
считается. Проверяются оба признака сразу: и ровно это окно, и аварийный тип заявки BK (urgent_bk_types
конфига) — в четырёх файлах выгрузки все 14 таких окон стоят у «Глобальной проблемы» и ни у кого больше. Любое
другое окно, накрывающее смену, остаётся обычным окном не по сетке: назвать его клиенту всё равно нельзя.
На четырёх реальных файлах выгрузки и на сгенерированном регионе проверка не находит ничего — это зафиксировано
тестом, чтобы она не съела настоящие данные.
"""

from __future__ import annotations

from dataclasses import replace

from app.domain.timeutil import DAY_MIN, fmt_hhmm
from app.domain.windows import is_slot, slots_text
from app.ingest.beeline_csv import RawFile, RawRequestRow
from app.synth.config import SynthConfig
from app.synth.requests import norm_duration

# Сколько окон не по сетке отчёт показывает примерами: остальные стоят числом.
OFF_GRID_EXAMPLES = 3

# «Весь день» выгрузки: окно аварии ровно 00:01–23:59.
ALL_DAY = (1, DAY_MIN - 1)

# Горизонт планирования: время в модели OR-Tools меряется в пределах двух суток (app/solvers/ortools_solver.py,
# ёмкость измерения Time). Окно дальше не просто бесполезно — на нём падает расчёт всего дня, поэтому строка
# с таким окном выпадает так же, как строка с пустым окном.
HORIZON_MIN = 2 * DAY_MIN


def _window(row: RawRequestRow) -> str:
    return f"{fmt_hhmm(row.window_start)}–{fmt_hhmm(row.window_end)}"


def day_bounds(cfg: SynthConfig) -> tuple[int, int] | None:
    """Рабочий день смен конфига; None — смен нет, тогда про рабочий день сказать нечего."""
    if not cfg.shifts:
        return None
    return min(shift.start for shift in cfg.shifts), max(shift.end for shift in cfg.shifts)


def check_windows(cfg: SynthConfig, raw: RawFile) -> RawFile:
    """Файл с проверенными окнами: пустое окно уносит строку в skipped, остальное — в window_warnings.

    Сами окна не меняются: ни одно замечание не правит ни начало, ни конец. Строки перенумеровываются так же,
    как при пропуске повторов (app/api/ingest_service.py), чтобы row_index остался подряд.
    """
    grid = cfg.window_grid
    day = day_bounds(cfg)
    rows: list[RawRequestRow] = []
    skipped = list(raw.skipped)
    warnings: list[str] = []
    off_grid: list[RawRequestRow] = []
    noted: set[int] = set()  # строки, о которых уже сказано отдельно: в примеры сетки они не идут
    for row in raw.rows:
        if row.window_end <= row.window_start:
            skipped.append(
                f"строка {row.line_no}: у заявки {row.request_id} окно {_window(row)} кончается не позже "
                f"начала, строка пропущена"
            )
            continue
        if row.window_end > HORIZON_MIN:
            skipped.append(
                f"строка {row.line_no}: у заявки {row.request_id} окно {_window(row)} дальше горизонта "
                f"планирования (до {fmt_hhmm(HORIZON_MIN)}), строка пропущена"
            )
            continue
        rows.append(replace(row, row_index=len(rows)))
        if (row.window_start, row.window_end) == ALL_DAY and row.type_bk in cfg.urgent_bk_types:
            # «Весь день» аварии: так выгрузка помечает данные, это не ошибка, и замечания тут нет. Проверка
            # стоит до рабочего дня, чтобы не зависеть от того, заданы ли в конфиге смены.
            continue
        if day is not None:
            day_start, day_end = day
            if row.window_end <= day_start or row.window_start >= day_end:
                warnings.append(
                    f"строка {row.line_no}: у заявки {row.request_id} окно {_window(row)} вне рабочего дня "
                    f"{fmt_hhmm(day_start)}–{fmt_hhmm(day_end)} — приехать в него некому"
                )
                noted.add(row.line_no)
        norm = norm_duration(cfg, row.type_bk)
        length = row.window_end - row.window_start
        if length < norm:
            kind = f" по типу «{row.type_bk}»" if row.type_bk else ""
            warnings.append(
                f"строка {row.line_no}: у заявки {row.request_id} окно приезда {_window(row)} — всего "
                f"{length} мин, бригада должна попасть ровно в них, а работ{kind} на {norm} мин"
            )
            noted.add(row.line_no)
        if grid and not is_slot(grid, row.window_start, row.window_end):
            off_grid.append(row)
    if off_grid:
        # В примеры идут окна, о которых выше не сказано отдельно: две строки об одном окне диспетчеру ничего
        # не добавят. Если таких не осталось, показываем любые — число всё равно про все окна не по сетке,
        # поэтому оно и не сходится с примерами, о чём сказано прямо в замечании.
        examples = [row for row in off_grid if row.line_no not in noted] or off_grid
        shown = "; ".join(
            f"строка {row.line_no} — заявка {row.request_id}, {_window(row)}"
            for row in examples[:OFF_GRID_EXAMPLES]
        )
        also = " (в том числе названные выше)" if any(row.line_no in noted for row in off_grid) else ""
        warnings.append(
            f"окна не по сетке: {len(off_grid)} из {len(rows)}{also}, например {shown}. "
            f"Слоты сетки: {slots_text(grid)}"
        )
    return replace(raw, rows=rows, skipped=skipped, window_warnings=warnings)
