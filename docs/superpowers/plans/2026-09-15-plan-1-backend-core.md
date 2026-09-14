# План 1: ядро backend (данные, синтез, солверы) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Из сырых CSV Билайна получить воспроизводимые бандлы трёх регионов и два плана на каждый (базовый FCFS строго по ТЗ и оптимизированный OR-Tools) с обязательными метриками и сравнением с реальным распределением диспетчеров.

**Architecture:** Python-пакет `backend/app` без веб-слоя. `domain` задаёт pydantic-модели, которые одновременно служат схемой JSON. `ingest` читает CSV Билайна и геокодирует адреса через Nominatim с кэшем в git. `geo` строит матрицы времени и расстояния по типу транспорта и часу суток (OSRM или гаверсинус). `solvers` содержит одну функцию симуляции маршрута, которая проверяет все ограничения, и два солвера поверх неё. `synth` досинтезирует инженеров, длительности, приоритеты и транспорт по YAML-конфигу и собирает бандлы CLI-командой `prepare` с самопроверкой требования ТЗ о конфликте.

**Tech Stack:** Python 3.12, uv, pydantic 2, OR-Tools 9.15, httpx, PyYAML, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-15-field-service-routing-design.md` и `docs/superpowers/specs/2026-09-15-api-contract.md`. Конспект ТЗ и датасета: `research/tz-and-data-notes.md`.

## Global Constraints

- Python `>=3.12,<3.13`. Зависимости и все команды только через `uv` из каталога `backend`.
- OR-Tools импортируется как `from ortools.constraint_solver import pywrapcp, routing_enums_pb2`. Модуля `ortools.routing` в pip-пакете 9.15 нет. `SetAllowedVehiclesForIndex` из Python не принимает список, используется `routing.VehicleVar(index).SetValues([-1, ...])`. `ReadAssignmentFromRoutes` принимает индексы менеджера, а не номера узлов.
- Время в коде: int минут от полуночи. В JSON: строка `HH:MM` через тип `HHMM`.
- Единицы: минуты (int), километры (float, 2 знака), широта и долгота.
- Все тексты, которые увидит диспетчер, на русском.
- Любое ограничение (навык, транспорт, окно, смена, отмена) проверяется одной функцией `simulate_route`. Солверы и объяснения не дублируют проверки.
- Синтетические значения берутся только из `backend/config/synth_config.yaml` и `backend/config/traffic_profile.yaml`. Случайность только через `random.Random(f"{seed}:<назначение>:<ключ>")`.
- Тесты не ходят в сеть: `httpx.MockTransport` или фейковые классы.
- Сообщение каждого коммита заканчивается строками:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` и
  `Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ`.

## Карта файлов

| Файл | Ответственность |
|---|---|
| `backend/pyproject.toml` | зависимости, pytest, ruff |
| `backend/app/domain/timeutil.py` | `HH:MM` <-> минуты, тип `HHMM` |
| `backend/app/domain/enums.py` | навыки, транспорт, приоритеты, события, коды причин, русские подписи |
| `backend/app/domain/models.py` | `Request`, `Engineer`, `Office`, `Event`, `Visit`, `Route`, `Unassigned`, `Metrics`, `Plan`, `Bundle` |
| `backend/app/ingest/bundle.py` | чтение и запись `bundle.json` |
| `backend/app/ingest/beeline_csv.py` | чтение CSV Билайна (cp1251/utf-8), строка офиса, мусорные строки |
| `backend/app/ingest/address.py` | разбор адресов Билайна, варианты запросов к геокодеру |
| `backend/app/ingest/geocode.py` | клиент Nominatim, JSON-кэш, цепочка «дом, улица, район» |
| `backend/app/geo/haversine.py` | расстояние по прямой |
| `backend/app/geo/kvcache.py` | SQLite key-value кэш |
| `backend/app/geo/osrm.py` | клиент OSRM table/route |
| `backend/app/geo/matrix.py` | базовая матрица, модель транспорта, профиль пробок, `TravelTimes` |
| `backend/config/traffic_profile.yaml` | коэффициенты пробок по часам |
| `backend/app/solvers/problem.py` | `Problem`, `EngineerState`, `make_problem` |
| `backend/app/solvers/simulate.py` | прогон маршрута и проверка ограничений |
| `backend/app/solvers/eligibility.py` | фильтр навык/транспорт/доступность |
| `backend/app/solvers/reasons.py` | причина неназначения языком диспетчера |
| `backend/app/solvers/metrics.py` | обязательные метрики |
| `backend/app/solvers/assemble.py` | план из последовательностей заявок |
| `backend/app/solvers/fcfs.py` | базовый вариант из ТЗ |
| `backend/app/solvers/ortools_solver.py` | оптимизированный план |
| `backend/config/synth_config.yaml` | все правила досинтеза и регионы |
| `backend/app/synth/config.py` | pydantic-схема конфига |
| `backend/app/synth/requests.py` | заявки, длительность, приоритет, транспорт, сверка файлов |
| `backend/app/synth/engineers.py` | инженеры из истории бригад: навыки, смены, транспорт |
| `backend/app/synth/events.py` | демо-события |
| `backend/app/synth/control.py` | план диспетчеров |
| `backend/app/synth/prepare.py` | CLI сборки бандлов, отчёт, самопроверка |
| `backend/tests/helpers.py` | мини-задачи для тестов солверов |

---

### Task 1: Каркас backend и перенос данных

**Files:**
- Create: `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/domain/__init__.py`, `backend/app/ingest/__init__.py`, `backend/app/geo/__init__.py`, `backend/app/solvers/__init__.py`, `backend/app/synth/__init__.py`, `backend/tests/__init__.py`, `.gitignore`
- Move: шесть файлов `data/*.utf8.csv` в `data/raw/<region>_<kind>.csv`

**Interfaces:**
- Consumes: ничего.
- Produces: каталог `backend` с рабочим `uv run pytest`; сырые данные по путям `data/raw/east_control.csv`, `data/raw/east_synthetic.csv`, `data/raw/south_east_control.csv`, `data/raw/south_east_synthetic.csv`, `data/raw/south_center_control.csv`, `data/raw/south_center_synthetic.csv` (UTF-8, разделитель `;`).

- [ ] **Step 1: Установить uv, если его нет**

```bash
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

Expected: строка вида `uv 0.x.y`.

- [ ] **Step 2: Перенести сырые файлы под ASCII-имена**

```bash
mkdir -p data/raw
git mv "data/Восток Контрольное распределение..utf8.csv" data/raw/east_control.csv
git mv "data/Восток Синтетические данные.utf8.csv" data/raw/east_synthetic.csv
git mv "data/Юго-восток Контрольное распределение.utf8.csv" data/raw/south_east_control.csv
git mv "data/Юго-восток Синтетические данные.utf8.csv" data/raw/south_east_synthetic.csv
git mv "data/Югоцентр Контрольное распределение..utf8.csv" data/raw/south_center_control.csv
git mv "data/Югоцентр Синтетические данные.utf8.csv" data/raw/south_center_synthetic.csv
ls data/raw
```

Expected: шесть файлов в `data/raw`.

- [ ] **Step 3: Создать `backend/pyproject.toml`**

`backend/pyproject.toml`:

```toml
[project]
name = "routing-backend"
version = "0.1.0"
description = "Планирование маршрутов выездных инженеров (кейс Билайн Бизнес)"
requires-python = ">=3.12,<3.13"
dependencies = [
    "httpx>=0.27",
    "ortools>=9.15,<10",
    "pydantic>=2.8",
    "pyyaml>=6.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "ruff>=0.6",
]

[tool.uv]
package = false

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"

[tool.ruff]
line-length = 110
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]
ignore = ["E501"]  # длинные русские строки сообщений; код форматирует ruff format
```

- [ ] **Step 4: Создать пустые пакеты и `.gitignore`**

```bash
mkdir -p backend/app/domain backend/app/ingest backend/app/geo backend/app/solvers backend/app/synth backend/config backend/tests
touch backend/app/__init__.py backend/app/domain/__init__.py backend/app/ingest/__init__.py backend/app/geo/__init__.py backend/app/solvers/__init__.py backend/app/synth/__init__.py backend/tests/__init__.py
cat > .gitignore <<'GITIGNORE'
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
data/cache.sqlite
node_modules/
frontend/dist/
.env
GITIGNORE
```

- [ ] **Step 5: Поставить зависимости**

Run: `cd backend && uv sync`
Expected: uv скачивает Python 3.12 при необходимости, создаёт `backend/.venv`, в выводе есть `ortools`, `pydantic`, `pytest`.

- [ ] **Step 6: Проверить, что pytest запускается**

Run: `cd backend && uv run pytest`
Expected: `no tests ran` (код выхода 5, это нормально для пустого набора).

- [ ] **Step 7: Commit**

```bash
git add .gitignore backend data/raw
git commit -m "chore: scaffold backend package and move raw Beeline data" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 2: Время, перечисления и доменная модель

**Files:**
- Create: `backend/app/domain/timeutil.py`, `backend/app/domain/enums.py`, `backend/app/domain/models.py`, `backend/app/ingest/bundle.py`
- Test: `backend/tests/test_domain.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `parse_hhmm(value: str | int) -> int` (минуты не ограничены сверху: визит в плане диспетчеров может уйти за полночь), `fmt_hhmm(minutes: int) -> str`, `parse_beeline_datetime(value: str) -> int`, константа `DAY_MIN = 1440`, тип `HHMM` (int в Python, `"HH:MM"` в JSON).
  - `Skill` (`local`, `connection`, `emergency`), `Transport` (`car`, `foot`, `bike`, `public`), `Priority`, `RequestStatus`, `EventType` (`urgent`, `cancel`, `restore`, `engineer_unavailable`), `ReasonCode` (`no_skill`, `no_transport`, `does_not_fit_window_or_shift`, `no_free_engineer_in_window`, `address_not_found`), словари `SKILL_RU`, `TRANSPORT_RU`, `PRIORITY_RU`.
  - Модели `Request`, `Engineer`, `Office`, `Event`, `Visit`, `Route`, `Unassigned`, `Metrics`, `Plan`, `Bundle` с полями ровно как в коде ниже.
  - `save_bundle(bundle: Bundle, path: Path) -> None`, `load_bundle(path: Path) -> Bundle`.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/test_domain.py`:

```python
import pytest
from pydantic import BaseModel, ValidationError

from app.domain.enums import EventType, Skill, Transport
from app.domain.models import Bundle, Engineer, Event, Office, Request
from app.domain.timeutil import HHMM, fmt_hhmm, parse_beeline_datetime, parse_hhmm


def test_parse_hhmm_accepts_strings_and_minutes():
    assert parse_hhmm("09:30") == 570
    assert parse_hhmm("0:01") == 1
    assert parse_hhmm(600) == 600


@pytest.mark.parametrize("bad", ["9-30", "25:61", "", True, -5])
def test_parse_hhmm_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_hhmm(bad)


def test_fmt_hhmm_pads_and_allows_after_midnight():
    assert fmt_hhmm(570) == "09:30"
    assert fmt_hhmm(1470) == "24:30"


def test_hhmm_allows_very_late_visits_in_dispatcher_plans():
    assert parse_hhmm(2933) == 2933
    assert parse_hhmm(fmt_hhmm(2933)) == 2933


def test_parse_beeline_datetime_drops_date():
    assert parse_beeline_datetime("17.08.2026 23:59") == 1439
    assert parse_beeline_datetime("17.08.2026 0:01") == 1


def test_hhmm_field_is_int_in_python_and_string_in_json():
    class Model(BaseModel):
        t: HHMM

    model = Model(t="10:05")
    assert model.t == 605
    assert model.model_dump() == {"t": 605}
    assert model.model_dump(mode="json") == {"t": "10:05"}


def _request(**overrides):
    data = dict(
        id="R1",
        address="Москва",
        duration_min=30,
        window_start="10:00",
        window_end="12:00",
        skill=Skill.LOCAL,
    )
    data.update(overrides)
    return Request(**data)


def test_request_rejects_inverted_window():
    with pytest.raises(ValidationError):
        _request(window_start="12:00", window_end="10:00")


def test_engineer_requires_one_to_three_skills():
    base = dict(
        id="E1",
        name="Иванов",
        start_lat=55.7,
        start_lon=37.6,
        shift_start="09:00",
        shift_end="18:00",
        transport=Transport.CAR,
    )
    with pytest.raises(ValidationError):
        Engineer(**base, skills=[])
    assert Engineer(**base, skills=[Skill.LOCAL]).skills == [Skill.LOCAL]


def test_event_payload_rules():
    with pytest.raises(ValidationError):
        Event(type=EventType.URGENT, time="13:00")
    with pytest.raises(ValidationError):
        Event(type=EventType.CANCEL, time="13:00")
    with pytest.raises(ValidationError):
        Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00")
    event = Event(type=EventType.URGENT, time="13:00", request=_request())
    assert event.request.id == "R1"


def test_bundle_json_roundtrip():
    bundle = Bundle(
        region="east",
        office=Office(region="east", title="Восток", address="Москва", lat=55.7, lon=37.6),
        requests=[_request()],
        engineers=[],
    )
    restored = Bundle.model_validate_json(bundle.model_dump_json())
    assert restored == bundle
    assert '"window_start":"10:00"' in bundle.model_dump_json()


def test_save_and_load_bundle(tmp_path):
    from app.ingest.bundle import load_bundle, save_bundle

    bundle = Bundle(
        region="east",
        office=Office(region="east", title="Восток", address="Москва", lat=55.7, lon=37.6),
        requests=[_request()],
        engineers=[],
    )
    path = tmp_path / "bundles" / "east" / "bundle.json"
    save_bundle(bundle, path)
    assert load_bundle(path) == bundle
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_domain.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.domain.timeutil'`.

- [ ] **Step 3: Реализовать `timeutil.py`**

`backend/app/domain/timeutil.py`:

```python
"""Время внутри рабочего дня: минуты от полуночи в коде, HH:MM в JSON."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer

DAY_MIN = 24 * 60

# Часы не ограничены сверху: в плане диспетчеров визит может уйти за полночь и дальше.
_HHMM = re.compile(r"^(\d{1,3}):(\d{2})$")


def parse_hhmm(value: str | int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"ожидается HH:MM, получено {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"минуты не могут быть отрицательными: {value}")
        return value
    match = _HHMM.match(str(value).strip())
    if not match:
        raise ValueError(f"ожидается HH:MM, получено {value!r}")
    hours, minutes = int(match.group(1)), int(match.group(2))
    if minutes > 59:
        raise ValueError(f"некорректное время: {value!r}")
    return hours * 60 + minutes


def fmt_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_beeline_datetime(value: str) -> int:
    """'17.08.2026 0:01' -> 1. Дата отбрасывается: данные за один день."""
    parts = value.strip().split()
    if len(parts) != 2:
        raise ValueError(f"ожидается 'ДД.ММ.ГГГГ Ч:ММ', получено {value!r}")
    return parse_hhmm(parts[1])


HHMM = Annotated[
    int,
    BeforeValidator(parse_hhmm),
    PlainSerializer(fmt_hhmm, return_type=str, when_used="json"),
]
```

- [ ] **Step 4: Реализовать `enums.py`**

`backend/app/domain/enums.py`:

```python
from __future__ import annotations

from enum import StrEnum


class Skill(StrEnum):
    LOCAL = "local"
    CONNECTION = "connection"
    EMERGENCY = "emergency"


class Transport(StrEnum):
    CAR = "car"
    FOOT = "foot"
    BIKE = "bike"
    PUBLIC = "public"


class Priority(StrEnum):
    NORMAL = "normal"
    URGENT = "urgent"


class RequestStatus(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class EventType(StrEnum):
    URGENT = "urgent"
    CANCEL = "cancel"
    RESTORE = "restore"
    ENGINEER_UNAVAILABLE = "engineer_unavailable"


class ReasonCode(StrEnum):
    NO_SKILL = "no_skill"
    NO_TRANSPORT = "no_transport"
    DOES_NOT_FIT = "does_not_fit_window_or_shift"
    NO_FREE_ENGINEER = "no_free_engineer_in_window"
    ADDRESS_NOT_FOUND = "address_not_found"


SKILL_RU = {
    Skill.LOCAL: "Локальные работы",
    Skill.CONNECTION: "Работы на подключение и дозаказы",
    Skill.EMERGENCY: "Аварийные работы",
}
TRANSPORT_RU = {
    Transport.CAR: "Автомобиль",
    Transport.FOOT: "Пешеход",
    Transport.BIKE: "Велосипед",
    Transport.PUBLIC: "Общественный транспорт",
}
PRIORITY_RU = {Priority.NORMAL: "Обычная", Priority.URGENT: "Срочная"}
```

- [ ] **Step 5: Реализовать `models.py`**

`backend/app/domain/models.py`:

```python
"""Доменная модель. Она же схема JSON-бандла и API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import EventType, Priority, ReasonCode, RequestStatus, Skill, Transport
from app.domain.timeutil import HHMM

GeocodePrecision = Literal["house", "street", "locality", "none"]


class Request(BaseModel):
    id: str
    address: str
    lat: float | None = None
    lon: float | None = None
    geocode_precision: GeocodePrecision = "none"
    district: str = ""
    duration_min: int = Field(gt=0)
    window_start: HHMM
    window_end: HHMM
    priority: Priority = Priority.NORMAL
    skill: Skill
    transport_required: Transport | None = None
    status: RequestStatus = RequestStatus.ACTIVE
    source_type_bk: str = ""
    source_type_hd: str = ""

    @model_validator(mode="after")
    def _window_order(self) -> Request:
        if self.window_end < self.window_start:
            raise ValueError("конец временного окна раньше начала")
        return self


class Engineer(BaseModel):
    id: str
    name: str
    start_lat: float
    start_lon: float
    shift_start: HHMM
    shift_end: HHMM
    skills: list[Skill] = Field(min_length=1, max_length=3)
    transport: Transport
    available: bool = True
    unavailable_from: HHMM | None = None

    @model_validator(mode="after")
    def _shift_order(self) -> Engineer:
        if self.shift_end <= self.shift_start:
            raise ValueError("конец смены должен быть позже начала")
        return self


class Office(BaseModel):
    region: str
    title: str
    address: str
    lat: float
    lon: float


class Event(BaseModel):
    type: EventType
    time: HHMM
    request: Request | None = None
    request_id: str | None = None
    engineer_id: str | None = None

    @model_validator(mode="after")
    def _payload(self) -> Event:
        if self.type == EventType.URGENT and self.request is None:
            raise ValueError("для срочной заявки нужен полный набор полей заявки")
        if self.type in (EventType.CANCEL, EventType.RESTORE) and not self.request_id:
            raise ValueError("для отмены или возврата нужен request_id")
        if self.type == EventType.ENGINEER_UNAVAILABLE and not self.engineer_id:
            raise ValueError("для недоступности инженера нужен engineer_id")
        return self


class Visit(BaseModel):
    request_id: str
    arrival: HHMM
    start: HHMM
    end: HHMM
    leg_km: float
    leg_min: int
    late_min: int = 0
    pinned: bool = False


class Route(BaseModel):
    engineer_id: str
    visits: list[Visit] = Field(default_factory=list)
    total_km: float = 0.0
    total_travel_min: int = 0


class Unassigned(BaseModel):
    request_id: str
    reason_code: ReasonCode
    reason_text: str


class Metrics(BaseModel):
    engineers_used: int
    km_per_engineer: dict[str, float]
    total_km: float
    assigned: int
    unassigned: int
    violations: int = 0


class Plan(BaseModel):
    solver: str
    routes: list[Route]
    unassigned: list[Unassigned]
    metrics: Metrics
    violations: list[str] = Field(default_factory=list)


class Bundle(BaseModel):
    region: str
    office: Office
    requests: list[Request]
    engineers: list[Engineer]
    events: list[Event] = Field(default_factory=list)
    control_plan: Plan | None = None
```

- [ ] **Step 6: Реализовать `ingest/bundle.py`**

`backend/app/ingest/bundle.py`:

```python
from __future__ import annotations

from pathlib import Path

from app.domain.models import Bundle


def save_bundle(bundle: Bundle, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")


def load_bundle(path: Path) -> Bundle:
    return Bundle.model_validate_json(Path(path).read_text(encoding="utf-8"))
```

- [ ] **Step 7: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_domain.py`
Expected: `15 passed`

- [ ] **Step 8: Commit**

```bash
git add backend/app/domain backend/app/ingest/bundle.py backend/tests/test_domain.py
git commit -m "feat(domain): add time helpers, enums, domain models and bundle IO" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 3: Чтение CSV Билайна

**Files:**
- Create: `backend/app/ingest/beeline_csv.py`
- Test: `backend/tests/test_beeline_csv.py`

**Interfaces:**
- Consumes: `parse_beeline_datetime` из Task 2.
- Produces:
  - `RawRequestRow(row_index: int, request_id: str, type_bk: str, type_hd: str, window_start: int, window_end: int, district: str, address: str, status_bk: str = "", crew: str = "")`, frozen dataclass. `row_index` это номер среди валидных строк с нуля: по нему синтетический файл сопоставляется с контрольным.
  - `RawFile(rows: list[RawRequestRow], office_address: str | None, is_control: bool, skipped: list[str])`.
  - `decode_bytes(data: bytes) -> str` (utf-8-sig, затем cp1251), `parse_beeline_csv(data: bytes) -> RawFile`. Строка с `Заявка` = «Адрес офиса» (регистр не важен) даёт `office_address` из колонки `Тип заявки BK`. Пустые строки пропускаются молча, строки без ID или окна попадают в `skipped` с номером строки файла.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/test_beeline_csv.py`:

```python
import pytest

from app.ingest.beeline_csv import parse_beeline_csv

SYNTHETIC = (
    "Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Гигабитное подключение\r\n"
    "74198;Подключение;Конвергенция абонента;17.08.2026 20:00;17.08.2026 22:00;Кузьминки;"
    "Город Москва, пр-кт.Волгоградский, д. 128 к 5;Нет\r\n"
    "50104;Глобальная проблема;Авария;17.08.2026 0:01;17.08.2026 23:59;Кашира;Кашира, ул.Победы, д. 9;Нет\r\n"
    ";;;;;;;\r\n"
    "99999;Локальная заявка;Нет линка;;;Таганский;Город Москва, пер.Маяковского, д. 2;Нет\r\n"
    "Адрес Офиса;г. Москва, ул Юных Ленинцев, д 83с 4;;;;;;\r\n"
)

CONTROL = (
    "Заявка;Тип заявки BK;Статус BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Бригада;Гигабитное подключение\r\n"
    "305927238;Подключение;Отменена;Конвергенция абонента;17.08.2026 20:00;17.08.2026 22:00;Кузьминки;"
    "Город Москва, пр-кт.Волгоградский, д. 128 к 5, кв. 1;Бригада Матвеев;Нет\r\n"
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
    assert raw.skipped == ["строка 5: нет номера заявки или временного окна"]


def test_parses_utf8_control_file_with_crew_and_status():
    raw = parse_beeline_csv(CONTROL.encode("utf-8"))
    assert raw.is_control
    row = raw.rows[0]
    assert (row.crew, row.status_bk) == ("Бригада Матвеев", "Отменена")


def test_rejects_file_without_required_columns():
    with pytest.raises(ValueError, match="нет колонок"):
        parse_beeline_csv(b"a;b\r\n1;2\r\n")
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_beeline_csv.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.ingest.beeline_csv'`.

- [ ] **Step 3: Реализовать**

`backend/app/ingest/beeline_csv.py`:

```python
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


@dataclass(frozen=True)
class RawFile:
    rows: list[RawRequestRow]
    office_address: str | None
    is_control: bool
    skipped: list[str] = field(default_factory=list)


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
            )
        )
    return RawFile(rows=rows, office_address=office, is_control="Бригада" in header, skipped=skipped)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_beeline_csv.py`
Expected: `3 passed`

- [ ] **Step 5: Проверить на реальных файлах**

Run:
```bash
cd backend && uv run python -c "
from pathlib import Path
from app.ingest.beeline_csv import parse_beeline_csv
for name in ['east', 'south_east', 'south_center']:
    s = parse_beeline_csv(Path(f'../data/raw/{name}_synthetic.csv').read_bytes())
    c = parse_beeline_csv(Path(f'../data/raw/{name}_control.csv').read_bytes())
    print(name, len(s.rows), len(c.rows), s.office_address, len(s.skipped))
"
```
Expected:
```
east 66 66 г. Москва, ул Юных Ленинцев, д 83с 4 0
south_east 83 83 г. Москва, ул Бирюлёвская, д 1с1 0
south_center 56 56 г.Москва проезд Симферопольский, д.7 0
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/ingest/beeline_csv.py backend/tests/test_beeline_csv.py
git commit -m "feat(ingest): parse Beeline CSV exports with office row and junk rows" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 4: Разбор адресов

Nominatim не находит адреса в формате Билайна («Город Москва, пр-кт.Волгоградский, д. 128 к 5»), но находит нормализованные («Москва, Волгоградский проспект, 128к5»). Разбор покрывает все форматы, найденные в трёх регионах: префиксные и суффиксные типы улиц, «г.Город Москва», «МО, г. Кашира …», адреса без запятых, порядковые числительные после названия, «Квартал 137а», корпуса и строения.

**Files:**
- Create: `backend/app/ingest/address.py`
- Test: `backend/tests/test_address.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `ParsedAddress(raw: str, city: str, street_type: str | None, street_name: str | None, house: str | None)`. `city` равен `"Москва"` или `"<Город>, Московская область"` для Домодедово, Каширы, Ступино.
  - `normalize_house(raw: str) -> str | None`, `parse_address(raw: str) -> ParsedAddress`.
  - `query_variants(parsed: ParsedAddress, district: str = "") -> list[tuple[str, str]]`: пары (запрос, точность) с точностью `house`, `street`, `locality` от точной к грубой.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/test_address.py`:

```python
import pytest

from app.ingest.address import normalize_house, parse_address, query_variants

CASES = [
    ("Город Москва, пр-кт.Волгоградский, д. 128 к 5, кв. 1", "Москва", "проспект", "Волгоградский", "128к5"),
    ("г.Город Москва, наб.Семеновская, д. 3/1к2", "Москва", "набережная", "Семеновская", "3/1к2"),
    ("Москва, проезд Орехово-Зуевский, д. 18/8", "Москва", "проезд", "Орехово-Зуевский", "18/8"),
    (
        "Домодедово, проезд.Советский 1-й, д. 1А",
        "Домодедово, Московская область",
        "проезд",
        "1-й Советский",
        "1А",
    ),
    (
        "обл.Московская область, г.Домодедово, пгт.Востряково-1, ул.Жуковского, д. 14/18",
        "Домодедово, Московская область",
        "улица",
        "Жуковского",
        "14/18",
    ),
    ("МО, г. Кашира Центральная ул. д. 21", "Кашира, Московская область", "улица", "Центральная", "21"),
    ("Москва Бирюлевская ул. д. 44", "Москва", "улица", "Бирюлевская", "44"),
    ("Москва Булатниковский пр-зд. д. 6к1", "Москва", "проезд", "Булатниковский", "6к1"),
    ("Город Москва, б-р.Самаркандский Квартал 137а, д. к5", "Москва", "бульвар", "Самаркандский", None),
    ("Город Москва, ш.Каширское, д. 136", "Москва", "шоссе", "Каширское", "136"),
    ("Город Москва, пр-кт.60-летия Октября, д. 18 к 2", "Москва", "проспект", "60-летия Октября", "18к2"),
    ("г. Москва, ул Юных Ленинцев, д 83с 4", "Москва", "улица", "Юных Ленинцев", "83с4"),
    ("г.Москва проезд Симферопольский, д.7", "Москва", "проезд", "Симферопольский", "7"),
]


@pytest.mark.parametrize(("raw", "city", "street_type", "street_name", "house"), CASES)
def test_parse_address_real_formats(raw, city, street_type, street_name, house):
    parsed = parse_address(raw)
    assert (parsed.city, parsed.street_type, parsed.street_name, parsed.house) == (
        city,
        street_type,
        street_name,
        house,
    )


def test_normalize_house_building_suffix():
    assert normalize_house("16 стр. 1") == "16с1"
    assert normalize_house("к5") is None


def test_query_variants_go_from_house_to_locality():
    parsed = parse_address("Город Москва, ул.Грайвороновская, д. 10 к 2")
    assert query_variants(parsed, "Текстильщики") == [
        ("Москва, Грайвороновская улица, 10к2", "house"),
        ("Москва, улица Грайвороновская, 10к2", "house"),
        ("Москва, Грайвороновская улица", "street"),
        ("Москва, улица Грайвороновская", "street"),
        ("район Текстильщики, Москва", "locality"),
    ]


def test_query_variants_for_town_fall_back_to_town():
    parsed = parse_address("Кашира, ул.Победы, д. 9")
    assert query_variants(parsed, "Кашира")[-1] == ("Кашира, Московская область", "locality")
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_address.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.ingest.address'`.

- [ ] **Step 3: Реализовать**

`backend/app/ingest/address.py`:

```python
"""Разбор адресов из выгрузки Билайна и построение запросов к геокодеру."""

from __future__ import annotations

import re
from dataclasses import dataclass

STREET_TYPES: dict[str, str] = {
    "ул": "улица",
    "пр-кт": "проспект",
    "пер": "переулок",
    "проезд": "проезд",
    "пр-зд": "проезд",
    "б-р": "бульвар",
    "наб": "набережная",
    "ш": "шоссе",
    "пл": "площадь",
    "туп": "тупик",
}
TOWNS_OUTSIDE_MOSCOW = ("Домодедово", "Кашира", "Ступино")

_TYPES = "|".join(sorted((re.escape(key) for key in STREET_TYPES), key=len, reverse=True))
_PREFIX_TYPE = re.compile(rf"^(?P<type>{_TYPES})(?:\.\s*|\s+)(?P<name>.+)$")
_SUFFIX_TYPE = re.compile(rf"^(?P<name>.+?)\s+(?P<type>{_TYPES})\.?$")
_HOUSE = re.compile(r"(?:^|[\s,])д\.?\s*(?P<house>(?:\d|к\d)[^,]*)$")
_FLAT = re.compile(r",?\s*кв\.\s*\S+\s*$")
_TOWN_PREFIX = re.compile(r"^(?:г\.\s*)?(?:Город\s+)?(?:Москва|Домодедово|Кашира|Ступино)\b\s*")
_ORDINAL_SUFFIX = re.compile(r"^(?P<rest>.+?)\s+(?P<ord>\d+-[йяе])$")
_QUARTER = re.compile(r"\s+Квартал\s+\S+$")


@dataclass(frozen=True)
class ParsedAddress:
    raw: str
    city: str
    street_type: str | None
    street_name: str | None
    house: str | None


def normalize_house(raw: str) -> str | None:
    value = raw.strip()
    value = re.sub(r"\s*стр\.\s*", "с", value)
    value = re.sub(r"\s*к\s*(?=\d)", "к", value)
    value = re.sub(r"\s+", "", value)
    return value if value[:1].isdigit() else None


def _split_street(chunk: str) -> tuple[str, str] | None:
    for pattern in (_PREFIX_TYPE, _SUFFIX_TYPE):
        match = pattern.match(chunk)
        if not match:
            continue
        name = _QUARTER.sub("", match.group("name").strip().rstrip("."))
        ordinal = _ORDINAL_SUFFIX.match(name)
        if ordinal:
            name = f"{ordinal.group('ord')} {ordinal.group('rest')}"
        return STREET_TYPES[match.group("type")], name
    return None


def parse_address(raw: str) -> ParsedAddress:
    text = _FLAT.sub("", raw.strip())
    city = "Москва"
    for town in TOWNS_OUTSIDE_MOSCOW:
        if re.search(rf"\b{town}\b", text):
            city = f"{town}, Московская область"
            break
    house = None
    match = _HOUSE.search(text)
    if match:
        house = normalize_house(match.group("house"))
        text = text[: match.start()].strip(" ,")
    street_type = street_name = None
    for part in reversed([piece.strip() for piece in text.split(",") if piece.strip()]):
        chunk = _TOWN_PREFIX.sub("", part).strip()
        if not chunk:
            continue
        split = _split_street(chunk)
        if split:
            street_type, street_name = split
            break
    return ParsedAddress(raw=raw, city=city, street_type=street_type, street_name=street_name, house=house)


def query_variants(parsed: ParsedAddress, district: str = "") -> list[tuple[str, str]]:
    """Запросы к геокодеру от самого точного к самому грубому: [(запрос, точность)]."""
    variants: list[tuple[str, str]] = []
    if parsed.street_name and parsed.street_type:
        name_first = f"{parsed.street_name} {parsed.street_type}"
        type_first = f"{parsed.street_type} {parsed.street_name}"
        if parsed.house:
            variants.append((f"{parsed.city}, {name_first}, {parsed.house}", "house"))
            variants.append((f"{parsed.city}, {type_first}, {parsed.house}", "house"))
        variants.append((f"{parsed.city}, {name_first}", "street"))
        variants.append((f"{parsed.city}, {type_first}", "street"))
    if parsed.city == "Москва":
        clean = re.sub(r"\s*-\s*", "-", district.replace("GPON", "")).strip()
        if clean:
            variants.append((f"район {clean}, Москва", "locality"))
    else:
        variants.append((parsed.city, "locality"))
    return variants
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_address.py`
Expected: `16 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingest/address.py backend/tests/test_address.py
git commit -m "feat(ingest): normalize Beeline address formats into geocoder queries" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 5: Геокодер Nominatim с кэшем в git

**Files:**
- Create: `backend/app/ingest/geocode.py`
- Test: `backend/tests/test_geocode.py`

**Interfaces:**
- Consumes: `parse_address`, `query_variants` из Task 4.
- Produces:
  - `GeoHit(lat: float, lon: float, category: str = "")`, `GeoResult(lat: float | None, lon: float | None, precision: str, query: str | None)`.
  - Протокол `Geocoder.lookup(query: str) -> GeoHit | None`.
  - `NominatimGeocoder(base_url="https://nominatim.openstreetmap.org", user_agent="beeline-routing-hackathon/0.1", min_interval_s=1.1, client=None, sleep=time.sleep, clock=time.monotonic)`: не чаще одного запроса в `min_interval_s`, поиск ограничен bbox Москвы и области.
  - `JsonGeocodeCache(path: Path)` с `get(query) -> tuple[bool, GeoHit | None]`, `put(query, hit | None)`, `save()`. Промахи тоже кэшируются как `null`.
  - `geocode_address(raw: str, district: str, geocoder: Geocoder | None, cache: JsonGeocodeCache) -> GeoResult`. С `geocoder=None` работает только по кэшу. Хит вне bbox отбрасывается. Хит по «дому» с категорией `highway` понижается до `street`. Сетевая ошибка не кэшируется.
  - `in_region(lat, lon) -> bool`, `MOSCOW_REGION_BBOX = (54.2, 35.1, 57.0, 40.3)`.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/test_geocode.py`:

```python
import json

import httpx

from app.ingest.geocode import GeoHit, JsonGeocodeCache, NominatimGeocoder, geocode_address


class FakeGeocoder:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def lookup(self, query):
        self.calls.append(query)
        return self.answers.get(query)


def test_falls_back_from_house_to_street(tmp_path):
    geocoder = FakeGeocoder({"Москва, Грайвороновская улица": GeoHit(55.72, 37.73, "highway")})
    cache = JsonGeocodeCache(tmp_path / "cache.json")
    result = geocode_address("Город Москва, ул.Грайвороновская, д. 10 к 2", "Текстильщики", geocoder, cache)
    assert (result.lat, result.lon, result.precision) == (55.72, 37.73, "street")
    assert geocoder.calls == [
        "Москва, Грайвороновская улица, 10к2",
        "Москва, улица Грайвороновская, 10к2",
        "Москва, Грайвороновская улица",
    ]


def test_house_hit_on_a_road_is_downgraded_to_street(tmp_path):
    geocoder = FakeGeocoder({"Москва, Грайвороновская улица, 10к2": GeoHit(55.72, 37.73, "highway")})
    result = geocode_address(
        "Город Москва, ул.Грайвороновская, д. 10 к 2", "", geocoder, JsonGeocodeCache(tmp_path / "c.json")
    )
    assert result.precision == "street"


def test_rejects_hits_outside_moscow_region(tmp_path):
    geocoder = FakeGeocoder(
        {
            "Москва, Грайвороновская улица, 10к2": GeoHit(59.93, 30.31, "building"),
            "Москва, улица Грайвороновская, 10к2": GeoHit(55.72, 37.73, "building"),
        }
    )
    result = geocode_address(
        "Город Москва, ул.Грайвороновская, д. 10 к 2", "", geocoder, JsonGeocodeCache(tmp_path / "c.json")
    )
    assert (result.lat, result.precision) == (55.72, "house")


def test_cache_persists_hits_and_misses_and_avoids_repeat_calls(tmp_path):
    path = tmp_path / "cache.json"
    geocoder = FakeGeocoder({"Кашира, Московская область": GeoHit(54.83, 38.15, "boundary")})
    cache = JsonGeocodeCache(path)
    geocode_address("Кашира, ул.Победы, д. 9", "Кашира", geocoder, cache)
    cache.save()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["Кашира, Московская область"] == [54.83, 38.15, "boundary"]
    assert stored["Кашира, Московская область, Победы улица, 9"] is None

    second = FakeGeocoder({})
    result = geocode_address("Кашира, ул.Победы, д. 9", "Кашира", second, JsonGeocodeCache(path))
    assert second.calls == []
    assert result.precision == "locality"


def test_without_geocoder_uses_cache_only(tmp_path):
    result = geocode_address("Кашира, ул.Победы, д. 9", "", None, JsonGeocodeCache(tmp_path / "c.json"))
    assert (result.lat, result.precision) == (None, "none")


def test_nominatim_client_sends_bounded_query_and_respects_rate_limit():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[{"lat": "55.70", "lon": "37.78", "category": "building"}])

    sleeps = []
    ticks = iter([0.0, 0.2, 0.2])
    geocoder = NominatimGeocoder(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
        clock=lambda: next(ticks),
    )
    assert geocoder.lookup("Москва, Волгоградский проспект, 128к5") == GeoHit(55.70, 37.78, "building")
    geocoder.lookup("второй запрос")
    params = seen[0].url.params
    assert params["bounded"] == "1" and params["format"] == "jsonv2" and params["countrycodes"] == "ru"
    assert seen[0].headers["User-Agent"].startswith("beeline-routing")
    assert len(sleeps) == 1 and abs(sleeps[0] - 0.9) < 1e-9
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_geocode.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.ingest.geocode'`.

- [ ] **Step 3: Реализовать**

`backend/app/ingest/geocode.py`:

```python
"""Геокодирование через Nominatim с кэшем в JSON-файле (кэш коммитится в git)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from app.ingest.address import parse_address, query_variants

# min_lat, min_lon, max_lat, max_lon: Москва и Московская область
MOSCOW_REGION_BBOX = (54.2, 35.1, 57.0, 40.3)


@dataclass(frozen=True)
class GeoHit:
    lat: float
    lon: float
    category: str = ""


@dataclass(frozen=True)
class GeoResult:
    lat: float | None
    lon: float | None
    precision: str
    query: str | None


class Geocoder(Protocol):
    def lookup(self, query: str) -> GeoHit | None: ...


def in_region(lat: float, lon: float) -> bool:
    min_lat, min_lon, max_lat, max_lon = MOSCOW_REGION_BBOX
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


class NominatimGeocoder:
    def __init__(
        self,
        base_url: str = "https://nominatim.openstreetmap.org",
        user_agent: str = "beeline-routing-hackathon/0.1",
        min_interval_s: float = 1.1,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._min_interval_s = min_interval_s
        self._client = client or httpx.Client(timeout=20.0)
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None

    def lookup(self, query: str) -> GeoHit | None:
        if self._last_call is not None:
            wait = self._min_interval_s - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        min_lat, min_lon, max_lat, max_lon = MOSCOW_REGION_BBOX
        response = self._client.get(
            f"{self._base_url}/search",
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "ru",
                "viewbox": f"{min_lon},{max_lat},{max_lon},{min_lat}",
                "bounded": 1,
            },
            headers={"User-Agent": self._user_agent},
        )
        self._last_call = self._clock()
        response.raise_for_status()
        items = response.json()
        if not items:
            return None
        item = items[0]
        return GeoHit(float(item["lat"]), float(item["lon"]), str(item.get("category", "")))


class JsonGeocodeCache:
    """запрос -> [lat, lon, category] или null (промах тоже кэшируется)."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._data: dict[str, list | None] = {}
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def get(self, query: str) -> tuple[bool, GeoHit | None]:
        if query not in self._data:
            return False, None
        value = self._data[query]
        return True, None if value is None else GeoHit(value[0], value[1], value[2])

    def put(self, query: str, hit: GeoHit | None) -> None:
        self._data[query] = None if hit is None else [hit.lat, hit.lon, hit.category]

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
        )


def geocode_address(raw: str, district: str, geocoder: Geocoder | None, cache: JsonGeocodeCache) -> GeoResult:
    for query, precision in query_variants(parse_address(raw), district):
        found, hit = cache.get(query)
        if not found:
            if geocoder is None:
                continue
            try:
                hit = geocoder.lookup(query)
            except httpx.HTTPError:
                continue
            cache.put(query, hit)
        if hit is None or not in_region(hit.lat, hit.lon):
            continue
        if precision == "house" and hit.category == "highway":
            precision = "street"
        return GeoResult(hit.lat, hit.lon, precision, query)
    return GeoResult(None, None, "none", None)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_geocode.py`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingest/geocode.py backend/tests/test_geocode.py
git commit -m "feat(ingest): geocode via Nominatim with fallback chain and JSON cache" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 6: Матрицы времени и расстояния

Правила (согласованы с пользователем): автомобиль берёт время OSRM и умножает на коэффициент пробок по часу окна заявки назначения; велосипед едет по автомобильному маршруту со скоростью 20 км/ч без пробок; пешеход идёт 5 км/ч по автомобильным километрам с коэффициентом 1.2; общественный транспорт считается по прямой с коэффициентом 1.3, 15 км/ч и 10 минутами ожидания. Без OSRM дорожное расстояние равно прямому на 1.3, автомобиль 25 км/ч.

**Files:**
- Create: `backend/app/geo/haversine.py`, `backend/app/geo/kvcache.py`, `backend/app/geo/osrm.py`, `backend/app/geo/matrix.py`, `backend/config/traffic_profile.yaml`
- Test: `backend/tests/test_geo.py`

**Interfaces:**
- Consumes: `Transport` из Task 2.
- Produces:
  - `haversine_km(lat1, lon1, lat2, lon2) -> float`.
  - `KVCache(path: Path | str)` с `get(key) -> str | None`, `set(key, value) -> None`; `":memory:"` для тестов.
  - `OsrmClient(base_url: str, client: httpx.Client | None = None, timeout: float = 30.0)` с `table(points: Sequence[tuple[lat, lon]]) -> (km, minutes)` (None в недостижимых ячейках), `route_geometry(points) -> list[[lon, lat]]`, `health() -> bool`; исключение `OsrmError`. Тип `LatLon = tuple[float, float]`.
  - `TravelModel` (frozen dataclass с полями `detour_factor=1.3`, `car_fallback_speed_kmh=25.0`, `foot_speed_kmh=5.0`, `foot_km_factor=1.2`, `bike_speed_kmh=20.0`, `public_speed_kmh=15.0`, `public_wait_min=10.0`).
  - `TrafficProfile(factors: dict[int, float])` с `factor_at(minute_of_day) -> float` и `TrafficProfile.load(path)`.
  - `BaseMatrix(road_km, car_min, straight_km, source)`; `build_base_matrix(points, model, osrm=None, cache=None) -> BaseMatrix` (кэш по хэшу координат, при ошибке OSRM фолбэк на гаверсинус).
  - `TravelTimes(base, model, traffic)` с `km(i, j, transport) -> float` и `minutes(i, j, transport, slot_min) -> int` (округление вверх).

- [ ] **Step 1: Написать падающий тест**

`backend/tests/test_geo.py`:

```python
import httpx
import pytest

from app.domain.enums import Transport
from app.geo.haversine import haversine_km
from app.geo.kvcache import KVCache
from app.geo.matrix import BaseMatrix, TrafficProfile, TravelModel, TravelTimes, build_base_matrix
from app.geo.osrm import OsrmClient

POINTS = [(55.7558, 37.6176), (55.7000, 37.7800)]


def test_haversine_moscow_to_saint_petersburg():
    assert haversine_km(55.7558, 37.6176, 59.9343, 30.3351) == pytest.approx(634, abs=5)


def test_kvcache_roundtrip():
    cache = KVCache(":memory:")
    assert cache.get("k") is None
    cache.set("k", "v")
    assert cache.get("k") == "v"


def _osrm(handler):
    return OsrmClient("http://osrm:5000", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_osrm_table_converts_units_and_keeps_unreachable_as_none():
    def handler(request):
        assert request.url.path == "/table/v1/driving/37.617600,55.755800;37.780000,55.700000"
        assert request.url.params["annotations"] == "duration,distance"
        return httpx.Response(
            200,
            json={"code": "Ok", "distances": [[0, 12000], [None, 0]], "durations": [[0, 1200], [None, 0]]},
        )

    km, minutes = _osrm(handler).table(POINTS)
    assert km == [[0.0, 12.0], [None, 0.0]]
    assert minutes == [[0.0, 20.0], [None, 0.0]]


def test_build_base_matrix_uses_osrm_fills_gaps_and_caches():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"code": "Ok", "distances": [[0, 12000], [None, 0]], "durations": [[0, 1200], [None, 0]]},
        )

    cache = KVCache(":memory:")
    model = TravelModel()
    first = build_base_matrix(POINTS, model, osrm=_osrm(handler), cache=cache)
    second = build_base_matrix(POINTS, model, osrm=_osrm(handler), cache=cache)
    assert first.source == "osrm" and first.road_km[0][1] == 12.0
    assert first.road_km[1][0] == pytest.approx(first.straight_km[1][0] * model.detour_factor)
    assert second == first
    assert len(calls) == 1


def test_build_base_matrix_falls_back_to_haversine_when_osrm_is_down():
    def handler(request):
        return httpx.Response(503)

    matrix = build_base_matrix(POINTS, TravelModel(), osrm=_osrm(handler))
    assert matrix.source == "haversine"


def test_travel_times_rules_per_transport():
    base = BaseMatrix(
        road_km=[[0, 10.0], [10.0, 0]],
        car_min=[[0, 20.0], [20.0, 0]],
        straight_km=[[0, 8.0], [8.0, 0]],
        source="osrm",
    )
    travel = TravelTimes(base, TravelModel(), TrafficProfile({17: 1.8}))
    assert travel.minutes(0, 1, Transport.CAR, slot_min=10 * 60) == 20
    assert travel.minutes(0, 1, Transport.CAR, slot_min=17 * 60 + 30) == 36
    assert travel.km(0, 1, Transport.BIKE) == 10.0
    assert travel.minutes(0, 1, Transport.BIKE, slot_min=17 * 60) == 30
    assert travel.km(0, 1, Transport.FOOT) == pytest.approx(12.0)
    assert travel.minutes(0, 1, Transport.FOOT, slot_min=0) == 144
    assert travel.km(0, 1, Transport.PUBLIC) == pytest.approx(10.4)
    assert travel.minutes(0, 1, Transport.PUBLIC, slot_min=0) == 52
    assert travel.minutes(1, 1, Transport.PUBLIC, slot_min=0) == 0


def test_traffic_profile_loads_yaml(tmp_path):
    path = tmp_path / "traffic.yaml"
    path.write_text("factors:\n  8: 1.7\n", encoding="utf-8")
    profile = TrafficProfile.load(path)
    assert profile.factor_at(8 * 60 + 59) == 1.7
    assert profile.factor_at(3 * 60) == 1.0
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_geo.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.geo.haversine'`.

- [ ] **Step 3: Реализовать `haversine.py`**

`backend/app/geo/haversine.py`:

```python
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))
```

- [ ] **Step 4: Реализовать `kvcache.py`**

`backend/app/geo/kvcache.py`:

```python
"""Простой key-value кэш в SQLite: матрицы OSRM и геометрии маршрутов."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class KVCache:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._conn.commit()

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
            self._conn.commit()
```

- [ ] **Step 5: Реализовать `osrm.py`**

`backend/app/geo/osrm.py`:

```python
"""Клиент OSRM: матрица расстояний/времени и геометрия маршрута (профиль driving)."""

from __future__ import annotations

from collections.abc import Sequence

import httpx

LatLon = tuple[float, float]


class OsrmError(RuntimeError):
    pass


class OsrmClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    @staticmethod
    def _coords(points: Sequence[LatLon]) -> str:
        return ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)

    def _get(self, path: str, params: dict[str, str]) -> dict:
        response = self._client.get(f"{self._base_url}{path}", params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "Ok":
            raise OsrmError(data.get("message") or data.get("code") or "OSRM error")
        return data

    def table(self, points: Sequence[LatLon]) -> tuple[list[list[float | None]], list[list[float | None]]]:
        """Возвращает (километры, минуты). None в ячейке: точка недостижима."""
        data = self._get(f"/table/v1/driving/{self._coords(points)}", {"annotations": "duration,distance"})
        km = [[None if value is None else value / 1000.0 for value in row] for row in data["distances"]]
        minutes = [[None if value is None else value / 60.0 for value in row] for row in data["durations"]]
        return km, minutes

    def route_geometry(self, points: Sequence[LatLon]) -> list[list[float]]:
        """Линия маршрута через точки по порядку, координаты [lon, lat] (GeoJSON)."""
        data = self._get(
            f"/route/v1/driving/{self._coords(points)}", {"overview": "full", "geometries": "geojson"}
        )
        return data["routes"][0]["geometry"]["coordinates"]

    def health(self) -> bool:
        try:
            self._get("/nearest/v1/driving/37.617600,55.755800", {})
        except (httpx.HTTPError, OsrmError):
            return False
        return True
```

- [ ] **Step 6: Реализовать `matrix.py`**

`backend/app/geo/matrix.py`:

```python
"""Матрицы времени и расстояния с учётом типа транспорта и часа суток."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from app.domain.enums import Transport
from app.geo.haversine import haversine_km
from app.geo.kvcache import KVCache
from app.geo.osrm import LatLon, OsrmClient, OsrmError


@dataclass(frozen=True)
class TravelModel:
    detour_factor: float = 1.3  # гаверсинус -> дорожное расстояние, если нет OSRM
    car_fallback_speed_kmh: float = 25.0
    foot_speed_kmh: float = 5.0
    foot_km_factor: float = 1.2  # пешеходный путь по автомобильному графу
    bike_speed_kmh: float = 20.0  # велосипед: маршрут как у авто, без пробок
    public_speed_kmh: float = 15.0
    public_wait_min: float = 10.0


@dataclass(frozen=True)
class TrafficProfile:
    """Коэффициент к времени OSRM (свободные дороги) по часу суток."""

    factors: dict[int, float] = field(default_factory=dict)

    def factor_at(self, minute_of_day: int) -> float:
        hour = max(0, min(23, minute_of_day // 60))
        return self.factors.get(hour, 1.0)

    @classmethod
    def load(cls, path: Path) -> TrafficProfile:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls({int(hour): float(value) for hour, value in (data.get("factors") or {}).items()})


@dataclass(frozen=True)
class BaseMatrix:
    road_km: list[list[float]]
    car_min: list[list[float]]
    straight_km: list[list[float]]
    source: str  # "osrm" | "haversine"


def _points_key(points: Sequence[LatLon]) -> str:
    payload = json.dumps([[round(lat, 6), round(lon, 6)] for lat, lon in points])
    return "osrm-table:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_base_matrix(
    points: Sequence[LatLon],
    model: TravelModel,
    osrm: OsrmClient | None = None,
    cache: KVCache | None = None,
) -> BaseMatrix:
    n = len(points)
    straight = [[haversine_km(*points[i], *points[j]) for j in range(n)] for i in range(n)]
    fallback_km = [[d * model.detour_factor for d in row] for row in straight]
    fallback_min = [[d / model.car_fallback_speed_kmh * 60.0 for d in row] for row in fallback_km]
    if osrm is not None and n > 0:
        key = _points_key(points)
        cached = cache.get(key) if cache is not None else None
        table = json.loads(cached) if cached is not None else None
        if table is None:
            try:
                table = osrm.table(points)
            except (httpx.HTTPError, OsrmError):
                table = None
            if table is not None and cache is not None:
                cache.set(key, json.dumps(table))
        if table is not None:
            km_raw, min_raw = table
            road_km = [
                [fallback_km[i][j] if km_raw[i][j] is None else km_raw[i][j] for j in range(n)]
                for i in range(n)
            ]
            car_min = [
                [fallback_min[i][j] if min_raw[i][j] is None else min_raw[i][j] for j in range(n)]
                for i in range(n)
            ]
            return BaseMatrix(road_km, car_min, straight, "osrm")
    return BaseMatrix(fallback_km, fallback_min, straight, "haversine")


class TravelTimes:
    def __init__(self, base: BaseMatrix, model: TravelModel, traffic: TrafficProfile) -> None:
        self.base = base
        self.model = model
        self.traffic = traffic

    def km(self, i: int, j: int, transport: Transport) -> float:
        if i == j:
            return 0.0
        if transport in (Transport.CAR, Transport.BIKE):
            return self.base.road_km[i][j]
        if transport == Transport.FOOT:
            return self.base.road_km[i][j] * self.model.foot_km_factor
        return self.base.straight_km[i][j] * self.model.detour_factor

    def minutes(self, i: int, j: int, transport: Transport, slot_min: int) -> int:
        """Время в пути, целые минуты (вверх). slot_min: время, по которому берётся коэффициент пробок."""
        if i == j:
            return 0
        if transport == Transport.CAR:
            raw = self.base.car_min[i][j] * self.traffic.factor_at(slot_min)
        elif transport == Transport.BIKE:
            raw = self.km(i, j, transport) / self.model.bike_speed_kmh * 60.0
        elif transport == Transport.FOOT:
            raw = self.km(i, j, transport) / self.model.foot_speed_kmh * 60.0
        else:
            raw = self.km(i, j, transport) / self.model.public_speed_kmh * 60.0 + self.model.public_wait_min
        return math.ceil(raw - 1e-9)
```

- [ ] **Step 7: Создать `backend/config/traffic_profile.yaml`**

`backend/config/traffic_profile.yaml`:

```yaml
# Коэффициент к времени проезда OSRM (свободные дороги) по часу суток для автомобиля.
# Допущение команды: типичная загрузка дорог Москвы в будний день. Час без записи = 1.0.
# Велосипед, пешеход и общественный транспорт коэффициент не используют.
factors:
  6: 1.1
  7: 1.4
  8: 1.7
  9: 1.7
  10: 1.5
  11: 1.4
  12: 1.4
  13: 1.4
  14: 1.4
  15: 1.5
  16: 1.6
  17: 1.8
  18: 1.8
  19: 1.6
  20: 1.4
  21: 1.2
  22: 1.1
```

- [ ] **Step 8: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_geo.py`
Expected: `7 passed`

- [ ] **Step 9: Commit**

```bash
git add backend/app/geo backend/config/traffic_profile.yaml backend/tests/test_geo.py
git commit -m "feat(geo): travel matrices per transport with OSRM, traffic profile and fallback" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 7: Постановка задачи, симуляция маршрута и фильтр допуска

Узлы матрицы: сначала стартовые точки инженеров в порядке входных данных, затем заявки с координатами. Это позволяет при перепланировании (План 2) стартовать инженера из узла последней выполненной заявки без пересчёта матрицы.

**Files:**
- Create: `backend/app/solvers/problem.py`, `backend/app/solvers/simulate.py`, `backend/app/solvers/eligibility.py`, `backend/tests/helpers.py`
- Test: `backend/tests/test_simulate.py`

**Interfaces:**
- Consumes: модели из Task 2, `TravelModel`, `TrafficProfile`, `TravelTimes`, `build_base_matrix`, `OsrmClient`, `KVCache` из Task 6.
- Produces:
  - `EngineerState(engineer: Engineer, start_node: int, available_from: int, available_until: int)` со свойством `active`; `initial_state(engineer, home_node) -> EngineerState`.
  - `Problem(requests, engineers, travel, states, open_request_ids, unplannable=[], pinned={}, previous_assignment={}, previous_order={}, now=0)` с методами `request(id)`, `has_request(id)`, `request_node(id)`, `home_node(engineer_id)`, `state(engineer_id)`, `travel_km(from_node, to_node, engineer) -> float`, `travel_min(from_node, to_node, engineer) -> int` (пробки по началу окна заявки назначения). `pinned: dict[engineer_id, list[Visit]]`, `previous_assignment: dict[request_id, engineer_id]`, `previous_order: dict[engineer_id, list[request_id]]`.
  - `make_problem(requests, engineers, *, model, traffic, osrm=None, cache=None) -> Problem`: заявки без координат уходят в `unplannable` с `ReasonCode.ADDRESS_NOT_FOUND`.
  - `SimResult(visits, violations, end_node, end_time)` со свойством `feasible`; `simulate_route(problem, state, request_ids) -> SimResult`.
  - `Exclusion` (`no_skill`, `no_transport`, `unavailable`), `exclusion(request, state) -> Exclusion | None`.
  - `tests/helpers.py`: `at(x_km, y_km)`, `req(...)`, `eng(...)`, `problem_of(requests, engineers)`.

- [ ] **Step 1: Написать хелперы тестов**

`backend/tests/helpers.py`:

```python
"""Мини-задачи для тестов солверов: координаты задаются в километрах от офиса."""

import math

from app.domain.enums import Priority, Skill, Transport
from app.domain.models import Engineer, Request
from app.geo.matrix import TrafficProfile, TravelModel
from app.solvers.problem import make_problem

OFFICE_LAT, OFFICE_LON = 55.75, 37.60
ALL_SKILLS = (Skill.LOCAL, Skill.CONNECTION, Skill.EMERGENCY)


def at(x_km: float, y_km: float) -> tuple[float, float]:
    lat = OFFICE_LAT + y_km / 111.0
    lon = OFFICE_LON + x_km / (111.0 * math.cos(math.radians(OFFICE_LAT)))
    return lat, lon


def req(
    request_id,
    x_km,
    y_km,
    window_start,
    window_end,
    *,
    skill=Skill.LOCAL,
    duration=30,
    priority=Priority.NORMAL,
    transport=None,
):
    lat, lon = at(x_km, y_km)
    return Request(
        id=request_id,
        address=f"адрес {request_id}",
        lat=lat,
        lon=lon,
        geocode_precision="house",
        duration_min=duration,
        window_start=window_start,
        window_end=window_end,
        priority=priority,
        skill=skill,
        transport_required=transport,
    )


def eng(
    engineer_id,
    *,
    skills=ALL_SKILLS,
    transport=Transport.CAR,
    shift=("09:00", "18:00"),
    available=True,
    unavailable_from=None,
):
    return Engineer(
        id=engineer_id,
        name=f"Инженер {engineer_id}",
        start_lat=OFFICE_LAT,
        start_lon=OFFICE_LON,
        shift_start=shift[0],
        shift_end=shift[1],
        skills=list(skills),
        transport=transport,
        available=available,
        unavailable_from=unavailable_from,
    )


def problem_of(requests, engineers):
    return make_problem(requests, engineers, model=TravelModel(), traffic=TrafficProfile({}))
```

- [ ] **Step 2: Написать падающий тест**

`backend/tests/test_simulate.py`:

```python
from app.domain.enums import ReasonCode, Skill, Transport
from app.domain.models import Request
from app.geo.matrix import TrafficProfile, TravelModel
from app.solvers.problem import make_problem
from app.solvers.simulate import simulate_route
from tests.helpers import eng, problem_of, req


def test_waits_for_window_and_computes_times():
    problem = problem_of([req("R1", 1, 0, "10:00", "12:00", duration=30)], [eng("E1")])
    sim = simulate_route(problem, problem.states[0], ["R1"])
    visit = sim.visits[0]
    assert sim.feasible
    assert visit.leg_min == 4  # 1 км * 1.3 / 25 км/ч = 3.12 мин -> 4
    assert (visit.arrival, visit.start, visit.end) == (9 * 60 + 4, 10 * 60, 10 * 60 + 30)
    assert visit.leg_km == 1.3


def test_reports_each_violated_constraint():
    problem = problem_of(
        [req("R1", 1, 0, "09:00", "09:01", skill=Skill.EMERGENCY, transport=Transport.CAR, duration=600)],
        [eng("E1", skills=[Skill.LOCAL], transport=Transport.FOOT)],
    )
    sim = simulate_route(problem, problem.states[0], ["R1"])
    text = " | ".join(sim.violations)
    assert not sim.feasible
    assert "нет навыка «Аварийные работы»" in text
    assert "нужен транспорт «Автомобиль»" in text
    assert "позже окна" in text
    assert "позже конца смены" in text


def test_unavailable_engineer_state_is_inactive():
    problem = problem_of(
        [], [eng("E1", available=False, unavailable_from="13:00"), eng("E2", available=False)]
    )
    assert problem.states[0].available_until == 13 * 60 and problem.states[0].active
    assert not problem.states[1].active


def test_requests_without_coordinates_become_unplannable():
    lost = Request(
        id="X", address="нигде", duration_min=30, window_start="10:00", window_end="12:00", skill=Skill.LOCAL
    )
    problem = make_problem([lost], [eng("E1")], model=TravelModel(), traffic=TrafficProfile({}))
    assert problem.open_request_ids == []
    assert problem.unplannable[0].reason_code == ReasonCode.ADDRESS_NOT_FOUND


def test_exclusion_checks_skill_then_transport_then_availability():
    from app.solvers.eligibility import Exclusion, exclusion

    request = req("R1", 1, 0, "10:00", "12:00", skill=Skill.CONNECTION, transport=Transport.CAR)
    problem = problem_of(
        [request],
        [
            eng("E1", skills=[Skill.LOCAL]),
            eng("E2", transport=Transport.BIKE),
            eng("E3", available=False),
            eng("E4"),
        ],
    )
    reasons = [exclusion(problem.request("R1"), state) for state in problem.states]
    assert reasons == [Exclusion.NO_SKILL, Exclusion.NO_TRANSPORT, Exclusion.UNAVAILABLE, None]
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_simulate.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.solvers.problem'`.

- [ ] **Step 4: Реализовать `problem.py`**

`backend/app/solvers/problem.py`:

```python
"""Постановка задачи для солверов: заявки, инженеры, матрица и состояние дня."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.enums import ReasonCode, RequestStatus
from app.domain.models import Engineer, Request, Unassigned, Visit
from app.geo.kvcache import KVCache
from app.geo.matrix import TrafficProfile, TravelModel, TravelTimes, build_base_matrix
from app.geo.osrm import OsrmClient


@dataclass
class EngineerState:
    engineer: Engineer
    start_node: int
    available_from: int
    available_until: int

    @property
    def active(self) -> bool:
        return self.available_until > self.available_from


def initial_state(engineer: Engineer, home_node: int) -> EngineerState:
    until = engineer.shift_end
    if not engineer.available:
        cutoff = engineer.unavailable_from if engineer.unavailable_from is not None else engineer.shift_start
        until = min(until, cutoff)
    return EngineerState(engineer, home_node, engineer.shift_start, until)


@dataclass
class Problem:
    requests: list[Request]  # только заявки с координатами, в порядке входных данных
    engineers: list[Engineer]  # в порядке входных данных
    travel: TravelTimes
    states: list[EngineerState]  # в порядке engineers
    open_request_ids: list[str]  # что нужно распределить, в порядке поступления
    unplannable: list[Unassigned] = field(default_factory=list)
    pinned: dict[str, list[Visit]] = field(default_factory=dict)
    previous_assignment: dict[str, str] = field(default_factory=dict)
    previous_order: dict[str, list[str]] = field(default_factory=dict)
    now: int = 0

    def __post_init__(self) -> None:
        offset = len(self.engineers)
        self._requests = {request.id: request for request in self.requests}
        self._nodes = {request.id: offset + k for k, request in enumerate(self.requests)}
        self._slot = [0] * offset + [request.window_start for request in self.requests]
        self._states = {state.engineer.id: state for state in self.states}

    def request(self, request_id: str) -> Request:
        return self._requests[request_id]

    def has_request(self, request_id: str) -> bool:
        return request_id in self._requests

    def request_node(self, request_id: str) -> int:
        return self._nodes[request_id]

    def home_node(self, engineer_id: str) -> int:
        return next(k for k, engineer in enumerate(self.engineers) if engineer.id == engineer_id)

    def state(self, engineer_id: str) -> EngineerState:
        return self._states[engineer_id]

    def travel_km(self, from_node: int, to_node: int, engineer: Engineer) -> float:
        return self.travel.km(from_node, to_node, engineer.transport)

    def travel_min(self, from_node: int, to_node: int, engineer: Engineer) -> int:
        """Коэффициент пробок берётся по началу окна заявки назначения."""
        return self.travel.minutes(from_node, to_node, engineer.transport, self._slot[to_node])


def make_problem(
    requests: list[Request],
    engineers: list[Engineer],
    *,
    model: TravelModel,
    traffic: TrafficProfile,
    osrm: OsrmClient | None = None,
    cache: KVCache | None = None,
) -> Problem:
    """Задача на начало дня: все инженеры в стартовых точках, все активные заявки открыты."""
    located = [r for r in requests if r.lat is not None and r.lon is not None]
    unplannable = [
        Unassigned(
            request_id=r.id,
            reason_code=ReasonCode.ADDRESS_NOT_FOUND,
            reason_text=f"Адрес не найден на карте: «{r.address}».",
        )
        for r in requests
        if (r.lat is None or r.lon is None) and r.status == RequestStatus.ACTIVE
    ]
    points = [(e.start_lat, e.start_lon) for e in engineers] + [(r.lat, r.lon) for r in located]
    travel = TravelTimes(build_base_matrix(points, model, osrm=osrm, cache=cache), model, traffic)
    states = [initial_state(engineer, k) for k, engineer in enumerate(engineers)]
    open_ids = [r.id for r in located if r.status == RequestStatus.ACTIVE]
    return Problem(located, list(engineers), travel, states, open_ids, unplannable)
```

- [ ] **Step 5: Реализовать `simulate.py`**

`backend/app/solvers/simulate.py`:

```python
"""Прогон маршрута инженера по времени: единая проверка всех ограничений."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.enums import SKILL_RU, TRANSPORT_RU, RequestStatus
from app.domain.models import Visit
from app.domain.timeutil import fmt_hhmm
from app.solvers.problem import EngineerState, Problem


@dataclass
class SimResult:
    visits: list[Visit]
    violations: list[str]
    end_node: int
    end_time: int

    @property
    def feasible(self) -> bool:
        return not self.violations


def simulate_route(problem: Problem, state: EngineerState, request_ids: Sequence[str]) -> SimResult:
    engineer = state.engineer
    node, clock = state.start_node, state.available_from
    visits: list[Visit] = []
    violations: list[str] = []
    for request_id in request_ids:
        request = problem.request(request_id)
        destination = problem.request_node(request_id)
        leg_min = problem.travel_min(node, destination, engineer)
        leg_km = problem.travel_km(node, destination, engineer)
        arrival = clock + leg_min
        start = max(arrival, request.window_start)
        late = max(0, start - request.window_end)
        end = start + request.duration_min
        if request.status != RequestStatus.ACTIVE:
            violations.append(f"{request_id}: заявка отменена")
        if request.skill not in engineer.skills:
            violations.append(f"{request_id}: у {engineer.name} нет навыка «{SKILL_RU[request.skill]}»")
        if request.transport_required is not None and request.transport_required != engineer.transport:
            violations.append(
                f"{request_id}: нужен транспорт «{TRANSPORT_RU[request.transport_required]}», "
                f"у {engineer.name} «{TRANSPORT_RU[engineer.transport]}»"
            )
        if late:
            violations.append(
                f"{request_id}: начало {fmt_hhmm(start)} позже окна до {fmt_hhmm(request.window_end)} на {late} мин"
            )
        if end > state.available_until:
            violations.append(
                f"{request_id}: окончание {fmt_hhmm(end)} позже конца смены {fmt_hhmm(state.available_until)}"
            )
        visits.append(
            Visit(
                request_id=request_id,
                arrival=arrival,
                start=start,
                end=end,
                leg_km=round(leg_km, 2),
                leg_min=leg_min,
                late_min=late,
            )
        )
        node, clock = destination, end
    return SimResult(visits=visits, violations=violations, end_node=node, end_time=clock)
```

- [ ] **Step 6: Реализовать `eligibility.py`**

`backend/app/solvers/eligibility.py`:

```python
"""Жёсткий фильтр «может ли инженер в принципе взять заявку»: навык, транспорт, доступность."""

from __future__ import annotations

from enum import StrEnum

from app.domain.models import Request
from app.solvers.problem import EngineerState


class Exclusion(StrEnum):
    NO_SKILL = "no_skill"
    NO_TRANSPORT = "no_transport"
    UNAVAILABLE = "unavailable"


def exclusion(request: Request, state: EngineerState) -> Exclusion | None:
    engineer = state.engineer
    if request.skill not in engineer.skills:
        return Exclusion.NO_SKILL
    if request.transport_required is not None and engineer.transport != request.transport_required:
        return Exclusion.NO_TRANSPORT
    if not state.active:
        return Exclusion.UNAVAILABLE
    return None
```

- [ ] **Step 7: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_simulate.py`
Expected: `5 passed`

- [ ] **Step 8: Commit**

```bash
git add backend/app/solvers/problem.py backend/app/solvers/simulate.py backend/app/solvers/eligibility.py backend/tests/helpers.py backend/tests/test_simulate.py
git commit -m "feat(solvers): problem model, route simulation and eligibility filter" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 8: Причины неназначения

Порядок проверок повторяет примеры ТЗ: нет навыка, нет транспорта, все подходящие недоступны, работа не помещается в окно или смену даже без других заявок, иначе все подходящие заняты.

**Files:**
- Create: `backend/app/solvers/reasons.py`
- Test: `backend/tests/test_reasons.py`

**Interfaces:**
- Consumes: `Problem`, `simulate_route` из Task 7.
- Produces: `unassigned_reason(problem: Problem, request_id: str, sequences: dict[str, list[str]]) -> Unassigned`. `sequences` это открытые (незакреплённые) последовательности заявок по инженерам в текущем плане.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/test_reasons.py`:

```python
from app.domain.enums import ReasonCode, Skill, Transport
from app.solvers.reasons import unassigned_reason
from tests.helpers import eng, problem_of, req


def _reason(requests, engineers, request_id, sequences=None):
    problem = problem_of(requests, engineers)
    return unassigned_reason(problem, request_id, sequences or {})


def test_no_skill():
    reason = _reason(
        [req("R1", 1, 0, "10:00", "12:00", skill=Skill.EMERGENCY)], [eng("E1", skills=[Skill.LOCAL])], "R1"
    )
    assert reason.reason_code == ReasonCode.NO_SKILL
    assert reason.reason_text == "Нет инженера с навыком «Аварийные работы»."


def test_no_transport():
    reason = _reason(
        [req("R1", 1, 0, "10:00", "12:00", transport=Transport.CAR)],
        [eng("E1", transport=Transport.FOOT)],
        "R1",
    )
    assert reason.reason_code == ReasonCode.NO_TRANSPORT


def test_all_suitable_engineers_unavailable():
    reason = _reason([req("R1", 1, 0, "10:00", "12:00")], [eng("E1", available=False)], "R1")
    assert reason.reason_code == ReasonCode.NO_FREE_ENGINEER
    assert "недоступны" in reason.reason_text


def test_does_not_fit_window_even_alone():
    reason = _reason([req("R1", 40, 0, "09:00", "09:30")], [eng("E1")], "R1")
    assert reason.reason_code == ReasonCode.DOES_NOT_FIT
    assert "окно 09:00–09:30" in reason.reason_text


def test_no_free_engineer_when_busy():
    requests = [
        req("R1", 1, 0, "10:00", "10:10", duration=120),
        req("R2", 1, 0, "10:00", "10:10", duration=30),
    ]
    reason = _reason(requests, [eng("E1")], "R2", sequences={"E1": ["R1"]})
    assert reason.reason_code == ReasonCode.NO_FREE_ENGINEER
    assert "освобождается Инженер E1 в 12:00" in reason.reason_text
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_reasons.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.solvers.reasons'`.

- [ ] **Step 3: Реализовать**

`backend/app/solvers/reasons.py`:

```python
"""Причина, по которой заявка осталась неназначенной, языком диспетчера."""

from __future__ import annotations

from app.domain.enums import SKILL_RU, TRANSPORT_RU, ReasonCode
from app.domain.models import Unassigned
from app.domain.timeutil import fmt_hhmm
from app.solvers.problem import Problem
from app.solvers.simulate import simulate_route


def unassigned_reason(problem: Problem, request_id: str, sequences: dict[str, list[str]]) -> Unassigned:
    request = problem.request(request_id)
    skill = SKILL_RU[request.skill]
    window = f"{fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}"

    def result(code: ReasonCode, text: str) -> Unassigned:
        return Unassigned(request_id=request_id, reason_code=code, reason_text=text)

    skilled = [s for s in problem.states if request.skill in s.engineer.skills]
    if not skilled:
        return result(ReasonCode.NO_SKILL, f"Нет инженера с навыком «{skill}».")
    with_transport = [
        s
        for s in skilled
        if request.transport_required is None or s.engineer.transport == request.transport_required
    ]
    if not with_transport:
        transport = TRANSPORT_RU[request.transport_required]
        return result(
            ReasonCode.NO_TRANSPORT, f"Нет инженера с навыком «{skill}» и транспортом «{transport}»."
        )
    active = [s for s in with_transport if s.active]
    if not active:
        names = ", ".join(s.engineer.name for s in with_transport)
        return result(ReasonCode.NO_FREE_ENGINEER, f"Все подходящие инженеры недоступны: {names}.")

    solo = [(s, simulate_route(problem, s, [request_id])) for s in active]
    fitting = [s for s, sim in solo if sim.feasible]
    if not fitting:
        state, sim = min(solo, key=lambda pair: (pair[1].visits[0].start, pair[1].visits[0].end))
        visit = sim.visits[0]
        return result(
            ReasonCode.DOES_NOT_FIT,
            f"Работа не помещается в окно {window} или в смену: даже без других заявок "
            f"{state.engineer.name} начнёт не раньше {fmt_hhmm(visit.start)} и закончит в "
            f"{fmt_hhmm(visit.end)} (смена до {fmt_hhmm(state.available_until)}).",
        )

    finish, state = min(
        ((simulate_route(problem, s, sequences.get(s.engineer.id, [])).end_time, s) for s in fitting),
        key=lambda pair: pair[0],
    )
    return result(
        ReasonCode.NO_FREE_ENGINEER,
        f"Нет свободных исполнителей на окно {window}: подходящие инженеры ({len(fitting)}) заняты другими "
        f"заявками. Раньше всех освобождается {state.engineer.name} в {fmt_hhmm(finish)}.",
    )
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_reasons.py`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/solvers/reasons.py backend/tests/test_reasons.py
git commit -m "feat(solvers): dispatcher-language reasons for unassigned requests" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 9: Метрики, сборка плана и базовый вариант FCFS

**Files:**
- Create: `backend/app/solvers/metrics.py`, `backend/app/solvers/assemble.py`, `backend/app/solvers/fcfs.py`
- Test: `backend/tests/test_fcfs.py`

**Interfaces:**
- Consumes: `Problem`, `simulate_route`, `exclusion` из Task 7, `unassigned_reason` из Task 8.
- Produces:
  - `compute_metrics(routes: list[Route], unassigned: list[Unassigned], violations: int) -> Metrics`: `engineers_used` считает инженеров хотя бы с одним визитом, `km_per_engineer` только по ним.
  - `build_plan(problem, solver: str, sequences: dict[str, list[str]], *, fixed_unassigned: dict[str, Unassigned] | None = None) -> Plan`: маршрут у каждого инженера (пустые тоже), закреплённые визиты `problem.pinned` идут первыми с `pinned=True`, нарушения собираются в `plan.violations`.
  - `FcfsSolver` с `name = "fcfs"`, `sequences(problem) -> dict[str, list[str]]`, `solve(problem) -> Plan`.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/test_fcfs.py`:

```python
from app.domain.enums import ReasonCode, Skill
from app.solvers.fcfs import FcfsSolver
from tests.helpers import eng, problem_of, req


def test_conflict_from_tz_sequential_assignment_uses_extra_engineer():
    # R1 поступила раньше, но её окно позже: после R1 инженер E1 уже не успевает к R2.
    problem = problem_of(
        [req("R1", 1, 0, "14:00", "16:00"), req("R2", 1, 0.1, "10:00", "12:00")],
        [eng("E1"), eng("E2")],
    )
    plan = FcfsSolver().solve(problem)
    routes = {route.engineer_id: [v.request_id for v in route.visits] for route in plan.routes}
    assert routes == {"E1": ["R1"], "E2": ["R2"]}
    assert plan.metrics.engineers_used == 2
    assert plan.metrics.violations == 0


def test_skips_engineers_without_skill_and_explains_unassigned():
    problem = problem_of(
        [
            req("R1", 1, 0, "10:00", "12:00", skill=Skill.CONNECTION),
            req("R2", 1, 0, "10:00", "12:00", skill=Skill.EMERGENCY),
        ],
        [eng("E1", skills=[Skill.LOCAL]), eng("E2", skills=[Skill.CONNECTION])],
    )
    plan = FcfsSolver().solve(problem)
    assert [v.request_id for v in plan.routes[1].visits] == ["R1"]
    assert [(u.request_id, u.reason_code) for u in plan.unassigned] == [("R2", ReasonCode.NO_SKILL)]
    assert plan.metrics.km_per_engineer == {"E2": 1.3}
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_fcfs.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.solvers.fcfs'`.

- [ ] **Step 3: Реализовать `metrics.py`**

`backend/app/solvers/metrics.py`:

```python
from __future__ import annotations

from app.domain.models import Metrics, Route, Unassigned


def compute_metrics(routes: list[Route], unassigned: list[Unassigned], violations: int) -> Metrics:
    used = [route for route in routes if route.visits]
    return Metrics(
        engineers_used=len(used),
        km_per_engineer={route.engineer_id: route.total_km for route in used},
        total_km=round(sum(route.total_km for route in used), 2),
        assigned=sum(len(route.visits) for route in used),
        unassigned=len(unassigned),
        violations=violations,
    )
```

- [ ] **Step 4: Реализовать `assemble.py`**

`backend/app/solvers/assemble.py`:

```python
"""Сборка плана из последовательностей заявок: время, пробег, причины, метрики."""

from __future__ import annotations

from app.domain.models import Plan, Route, Unassigned
from app.solvers.metrics import compute_metrics
from app.solvers.problem import Problem
from app.solvers.reasons import unassigned_reason
from app.solvers.simulate import simulate_route


def build_plan(
    problem: Problem,
    solver: str,
    sequences: dict[str, list[str]],
    *,
    fixed_unassigned: dict[str, Unassigned] | None = None,
) -> Plan:
    fixed_unassigned = fixed_unassigned or {}
    routes: list[Route] = []
    violations: list[str] = []
    placed: set[str] = set()
    for state in problem.states:
        engineer_id = state.engineer.id
        sim = simulate_route(problem, state, sequences.get(engineer_id, []))
        pinned = [visit.model_copy(update={"pinned": True}) for visit in problem.pinned.get(engineer_id, [])]
        visits = pinned + sim.visits
        violations.extend(sim.violations)
        placed.update(visit.request_id for visit in visits)
        routes.append(
            Route(
                engineer_id=engineer_id,
                visits=visits,
                total_km=round(sum(visit.leg_km for visit in visits), 2),
                total_travel_min=sum(visit.leg_min for visit in visits),
            )
        )
    unassigned = list(problem.unplannable)
    for request_id in problem.open_request_ids:
        if request_id not in placed:
            unassigned.append(
                fixed_unassigned.get(request_id) or unassigned_reason(problem, request_id, sequences)
            )
    return Plan(
        solver=solver,
        routes=routes,
        unassigned=unassigned,
        metrics=compute_metrics(routes, unassigned, len(violations)),
        violations=violations,
    )
```

- [ ] **Step 5: Реализовать `fcfs.py`**

`backend/app/solvers/fcfs.py`:

```python
"""Базовый вариант строго по ТЗ (п. 2.3).

Заявки берутся в порядке поступления и назначаются первому по порядку во входных данных
доступному инженеру, который удовлетворяет обязательным ограничениям. Порядок посещения
равен порядку назначения. Глобальной оптимизации нет.
"""

from __future__ import annotations

from app.domain.models import Plan
from app.solvers.assemble import build_plan
from app.solvers.eligibility import exclusion
from app.solvers.problem import Problem
from app.solvers.simulate import simulate_route


class FcfsSolver:
    name = "fcfs"

    def sequences(self, problem: Problem) -> dict[str, list[str]]:
        sequences: dict[str, list[str]] = {state.engineer.id: [] for state in problem.states}
        for request_id in problem.open_request_ids:
            request = problem.request(request_id)
            for state in problem.states:
                if exclusion(request, state) is not None:
                    continue
                candidate = sequences[state.engineer.id] + [request_id]
                if simulate_route(problem, state, candidate).feasible:
                    sequences[state.engineer.id] = candidate
                    break
        return sequences

    def solve(self, problem: Problem) -> Plan:
        return build_plan(problem, self.name, self.sequences(problem))
```

- [ ] **Step 6: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_fcfs.py`
Expected: `2 passed`

- [ ] **Step 7: Commit**

```bash
git add backend/app/solvers/metrics.py backend/app/solvers/assemble.py backend/app/solvers/fcfs.py backend/tests/test_fcfs.py
git commit -m "feat(solvers): mandatory metrics, plan assembly and FCFS baseline from the brief" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 10: Оптимизированный план на OR-Tools

Модель: у каждого активного инженера свой стартовый узел и общий фиктивный финиш (возврат не нужен). Время дуги = длительность работы в узле отправления плюс путь по транспорту инженера. Стоимость дуги = метры плюс штраф за перенос заявки к другому инженеру относительно предыдущего плана. Фиксированная стоимость инженера и штрафы за неназначение задают порядок целей: все заявки (срочные важнее), меньше инженеров, меньше километров. Матрицы времени и стоимости предвычисляются по каждому инженеру и регистрируются через `routing.RegisterTransitMatrix`, поэтому поиск не вызывает Python на каждой дуге. При планировании это дало за тот же лимит времени план на одного инженера меньше на Востоке и Юго-востоке по сравнению с Python-колбэками.

После поиска `repair_unassigned` жадно вставляет заявки, которые поиск не успел разместить за лимит времени: срочные первыми, в допустимую позицию с наименьшим приростом километров, предпочитая инженеров, у которых уже есть работа.

У инженера с закреплёнными визитами (`problem.pinned`) фиксированная стоимость нулевая: он уже работал сегодня и в метрике учтён в любом случае. Без этого при перепланировании даже холостое событие перетасовывает заявки, чтобы «сэкономить» инженера, который уже отработал утро (проверено при планировании: 5 переносов на событии, которое ничего не меняет).

**Files:**
- Create: `backend/app/solvers/ortools_solver.py`
- Test: `backend/tests/test_ortools_solver.py`

**Interfaces:**
- Consumes: `Problem`, `EngineerState`, `simulate_route`, `exclusion` из Task 7, `build_plan` из Task 9.
- Produces: `ObjectiveWeights(vehicle_fixed_cost=1_000_000, drop_normal=10_000_000, drop_urgent=100_000_000, reassignment=20_000)`; `OrToolsSolver(time_limit_s: int = 3, weights: ObjectiveWeights | None = None)` с `name = "ortools"`, `sequences(problem) -> dict[str, list[str]]`, `solve(problem) -> Plan`. Warm start берётся из `problem.previous_order`, штраф за перенос из `problem.previous_assignment`. Инженер с непустым `problem.pinned[engineer_id]` получает `SetFixedCostOfVehicle(0, v)`. `repair_unassigned(problem: Problem, sequences: dict[str, list[str]]) -> dict[str, list[str]]` возвращает новые последовательности и вызывается в конце `sequences`.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/test_ortools_solver.py`:

```python
from app.domain.enums import Priority, ReasonCode, Transport
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import OrToolsSolver, repair_unassigned
from tests.helpers import eng, problem_of, req


def _routes(plan):
    return {route.engineer_id: [v.request_id for v in route.visits] for route in plan.routes}


def test_resolves_tz_conflict_with_one_engineer():
    problem = problem_of(
        [req("R1", 1, 0, "14:00", "16:00"), req("R2", 1, 0.1, "10:00", "12:00")],
        [eng("E1"), eng("E2")],
    )
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert plan.metrics.engineers_used == 1
    assert max(_routes(plan).values(), key=len) == ["R2", "R1"]
    assert plan.metrics.violations == 0
    assert FcfsSolver().solve(problem).metrics.engineers_used == 2


def test_respects_transport_and_skill_constraints():
    problem = problem_of(
        [req("R1", 2, 0, "10:00", "12:00", transport=Transport.CAR), req("R2", 0.5, 0, "10:00", "12:00")],
        [eng("E1", transport=Transport.FOOT), eng("E2", transport=Transport.CAR)],
    )
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert "R1" in _routes(plan)["E2"]
    assert plan.metrics.violations == 0


def test_urgent_request_wins_when_only_one_fits():
    problem = problem_of(
        [
            req("N1", 1, 0, "10:00", "10:10", duration=60),
            req("U1", 1, 0.2, "10:00", "10:10", duration=60, priority=Priority.URGENT),
        ],
        [eng("E1")],
    )
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert _routes(plan)["E1"] == ["U1"]
    assert [(u.request_id, u.reason_code) for u in plan.unassigned] == [("N1", ReasonCode.NO_FREE_ENGINEER)]


def test_previous_assignment_breaks_ties():
    problem = problem_of([req("R1", 1, 0, "10:00", "12:00")], [eng("E1"), eng("E2")])
    problem.previous_assignment = {"R1": "E2"}
    problem.previous_order = {"E2": ["R1"]}
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert _routes(plan) == {"E1": [], "E2": ["R1"]}


def test_inactive_engineer_gets_no_work():
    problem = problem_of([req("R1", 1, 0, "10:00", "12:00")], [eng("E1", available=False), eng("E2")])
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert _routes(plan) == {"E1": [], "E2": ["R1"]}


def test_engineer_who_already_worked_today_has_no_fixed_cost():
    from dataclasses import replace

    from app.domain.models import Visit
    from app.solvers.problem import EngineerState

    base = problem_of(
        [req("P", 4, 0, "09:00", "12:00"), req("R1", 0.5, 0, "10:00", "12:00")],
        [eng("E1"), eng("E2")],
    )
    e1, e2 = base.states
    pinned_visit = Visit(request_id="P", arrival=560, start=560, end=600, leg_km=5.2, leg_min=20, pinned=True)
    problem = replace(
        base,
        states=[EngineerState(e1.engineer, base.request_node("P"), 600, e1.available_until), e2],
        open_request_ids=["R1"],
        pinned={"E1": [pinned_visit]},
        now=600,
    )
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert _routes(plan) == {"E1": ["P", "R1"], "E2": []}
    assert plan.metrics.engineers_used == 1


def test_repair_inserts_left_out_request_preferring_working_engineer():
    problem = problem_of(
        [req("R1", 1, 0, "10:00", "12:00"), req("R2", 1.2, 0, "10:00", "12:00")], [eng("E1"), eng("E2")]
    )
    assert repair_unassigned(problem, {"E1": ["R1"], "E2": []}) == {"E1": ["R1", "R2"], "E2": []}


def test_repair_puts_urgent_first_when_slots_are_scarce():
    problem = problem_of(
        [
            req("N1", 1, 0, "10:00", "10:10", duration=60),
            req("U1", 1, 0.1, "10:00", "10:10", duration=60, priority=Priority.URGENT),
        ],
        [eng("E1")],
    )
    assert repair_unassigned(problem, {"E1": []}) == {"E1": ["U1"]}
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_ortools_solver.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.solvers.ortools_solver'`.

- [ ] **Step 3: Реализовать**

`backend/app/solvers/ortools_solver.py`:

```python
"""Оптимизированный план: OR-Tools RoutingModel (VRPTW с навыками, транспортом и сменами).

Цель лексикографическая через веса: сначала назначить все заявки (срочные важнее),
затем задействовать меньше инженеров, затем меньше километров. При перепланировании
добавляется штраф за перенос заявки к другому инженеру, а у инженеров с закреплёнными
визитами фиксированная стоимость нулевая.
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.domain.enums import Priority
from app.domain.models import Plan
from app.solvers.assemble import build_plan
from app.solvers.eligibility import exclusion
from app.solvers.problem import EngineerState, Problem
from app.solvers.simulate import simulate_route

DAY_MIN = 24 * 60


@dataclass(frozen=True)
class ObjectiveWeights:
    vehicle_fixed_cost: int = 1_000_000  # условные 1000 км за каждого задействованного инженера
    drop_normal: int = 10_000_000
    drop_urgent: int = 100_000_000
    reassignment: int = 20_000  # условные 20 км за перенос заявки к другому инженеру


class OrToolsSolver:
    name = "ortools"

    def __init__(self, time_limit_s: int = 3, weights: ObjectiveWeights | None = None) -> None:
        self.time_limit_s = time_limit_s
        self.weights = weights or ObjectiveWeights()

    def solve(self, problem: Problem) -> Plan:
        return build_plan(problem, self.name, self.sequences(problem))

    def sequences(self, problem: Problem) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {state.engineer.id: [] for state in problem.states}
        vehicles = [state for state in problem.states if state.active]
        candidates = [
            request_id
            for request_id in problem.open_request_ids
            if any(exclusion(problem.request(request_id), state) is None for state in vehicles)
        ]
        if not vehicles or not candidates:
            return result
        solved = self._solve_model(problem, vehicles, candidates)
        for state, sequence in zip(vehicles, solved, strict=True):
            result[state.engineer.id] = _keep_feasible_prefix(problem, state, sequence)
        return repair_unassigned(problem, result)

    def _solve_model(
        self, problem: Problem, vehicles: list[EngineerState], candidates: list[str]
    ) -> list[list[str]]:
        weights = self.weights
        v_count = len(vehicles)
        end = v_count  # общий фиктивный финиш: возврат в стартовую точку не нужен
        nodes = (
            [state.start_node for state in vehicles] + [-1] + [problem.request_node(r) for r in candidates]
        )
        service = [0] * (v_count + 1) + [problem.request(r).duration_min for r in candidates]
        size = len(nodes)

        manager = pywrapcp.RoutingIndexManager(size, v_count, list(range(v_count)), [end] * v_count)
        routing = pywrapcp.RoutingModel(manager)

        time_callbacks = []
        for v, state in enumerate(vehicles):
            engineer = state.engineer
            time_matrix = [[0] * size for _ in range(size)]
            cost_matrix = [[0] * size for _ in range(size)]
            for a in range(size):
                if a == end:
                    continue
                for b in range(size):
                    if b == end or a == b:
                        time_matrix[a][b] = service[a]
                        continue
                    if b < v_count:
                        continue  # в стартовые узлы не въезжаем
                    time_matrix[a][b] = service[a] + problem.travel_min(nodes[a], nodes[b], engineer)
                    cost = round(problem.travel_km(nodes[a], nodes[b], engineer) * 1000)
                    previous = problem.previous_assignment.get(candidates[b - v_count - 1])
                    if previous is not None and previous != engineer.id:
                        cost += weights.reassignment
                    cost_matrix[a][b] = cost

            # Матрицы регистрируются в C++: поиск не вызывает Python на каждой дуге и успевает больше.
            time_callbacks.append(routing.RegisterTransitMatrix(time_matrix))
            routing.SetArcCostEvaluatorOfVehicle(routing.RegisterTransitMatrix(cost_matrix), v)

        routing.AddDimensionWithVehicleTransits(time_callbacks, DAY_MIN, 2 * DAY_MIN, False, "Time")
        time_dimension = routing.GetDimensionOrDie("Time")

        for k, request_id in enumerate(candidates):
            request = problem.request(request_id)
            index = manager.NodeToIndex(v_count + 1 + k)
            time_dimension.CumulVar(index).SetRange(request.window_start, request.window_end)
            allowed = [v for v, state in enumerate(vehicles) if exclusion(request, state) is None]
            # SetAllowedVehiclesForIndex не принимает list в Python-обёртке 9.15, поэтому VehicleVar
            routing.VehicleVar(index).SetValues([-1] + allowed)
            penalty = weights.drop_urgent if request.priority == Priority.URGENT else weights.drop_normal
            routing.AddDisjunction([index], penalty)

        for v, state in enumerate(vehicles):
            time_dimension.CumulVar(routing.Start(v)).SetRange(state.available_from, state.available_until)
            time_dimension.CumulVar(routing.End(v)).SetRange(state.available_from, state.available_until)
        routing.SetFixedCostOfAllVehicles(weights.vehicle_fixed_cost)
        for v, state in enumerate(vehicles):
            if problem.pinned.get(state.engineer.id):
                # Инженер уже работал сегодня и в метрике учтён в любом случае: не штрафуем за продолжение.
                routing.SetFixedCostOfVehicle(0, v)

        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        params.time_limit.FromSeconds(self.time_limit_s)
        routing.CloseModelWithParameters(params)

        initial = None
        if problem.previous_order:
            position = {request_id: v_count + 1 + k for k, request_id in enumerate(candidates)}
            hint = []
            for state in vehicles:
                sequence = [
                    r
                    for r in problem.previous_order.get(state.engineer.id, [])
                    if r in position and exclusion(problem.request(r), state) is None
                ]
                hint.append([manager.NodeToIndex(position[r]) for r in sequence])
            initial = routing.ReadAssignmentFromRoutes(hint, True)
        solution = (
            routing.SolveFromAssignmentWithParameters(initial, params)
            if initial is not None
            else routing.SolveWithParameters(params)
        )
        if solution is None:
            return [[] for _ in vehicles]

        routes: list[list[str]] = []
        for v in range(v_count):
            sequence = []
            index = solution.Value(routing.NextVar(routing.Start(v)))
            while not routing.IsEnd(index):
                sequence.append(candidates[manager.IndexToNode(index) - v_count - 1])
                index = solution.Value(routing.NextVar(index))
            routes.append(sequence)
        return routes


def _keep_feasible_prefix(problem: Problem, state: EngineerState, sequence: list[str]) -> list[str]:
    """Страховка от расхождения округлений: выкидываем визиты, которые не проходят симуляцию."""
    kept: list[str] = []
    for request_id in sequence:
        if simulate_route(problem, state, kept + [request_id]).feasible:
            kept.append(request_id)
    return kept


def repair_unassigned(problem: Problem, sequences: dict[str, list[str]]) -> dict[str, list[str]]:
    """Страховка от недосмотра поиска за лимит времени: жадно вставляет оставшиеся заявки.

    Срочные заявки идут первыми. Для каждой берётся допустимая позиция с наименьшим приростом
    километров, причём инженеры, у которых уже есть работа сегодня, предпочтительнее простаивающих.
    """
    result = {engineer_id: list(sequence) for engineer_id, sequence in sequences.items()}
    placed = {request_id for sequence in result.values() for request_id in sequence}
    pending = [request_id for request_id in problem.open_request_ids if request_id not in placed]
    pending.sort(key=lambda request_id: problem.request(request_id).priority != Priority.URGENT)
    for request_id in pending:
        request = problem.request(request_id)
        best: tuple[tuple[int, float], str, list[str]] | None = None
        for state in problem.states:
            if exclusion(request, state) is not None:
                continue
            engineer_id = state.engineer.id
            sequence = result.get(engineer_id, [])
            base_km = sum(visit.leg_km for visit in simulate_route(problem, state, sequence).visits)
            idle = 0 if sequence or problem.pinned.get(engineer_id) else 1
            for position in range(len(sequence) + 1):
                candidate = sequence[:position] + [request_id] + sequence[position:]
                sim = simulate_route(problem, state, candidate)
                if not sim.feasible:
                    continue
                key = (idle, sum(visit.leg_km for visit in sim.visits) - base_km)
                if best is None or key < best[0]:
                    best = (key, engineer_id, candidate)
        if best is not None:
            result[best[1]] = best[2]
    return result
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_ortools_solver.py`
Expected: `8 passed` (около 5 секунд из-за лимита времени поиска).

- [ ] **Step 5: Commit**

```bash
git add backend/app/solvers/ortools_solver.py backend/tests/test_ortools_solver.py
git commit -m "feat(solvers): OR-Tools VRPTW with skills, transport, shifts and warm start" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 11: Правила досинтеза: заявки, инженеры, события

Значения в конфиге откалиброваны при планировании, а не придуманы. Реальное распределение диспетчеров в основном было выполнено, значит хорошие параметры синтеза делают план диспетчеров почти допустимым в нашей модели. Первая оценка (смены 09–18 и 13–22, старт из офиса, случайный транспорт) давала плану диспетчеров до 77 заявок с нарушениями из 83, а все планы использовали всех инженеров. Сетка по длительностям, сменам, стартовой точке и транспорту из истории выбрала: длительности ×0.75 от первой оценки, одну смену 10:00–22:00, старт из медоида истории бригады, автомобиль у бригад, которым он требовался по истории. Не меняйте эти значения без повторной калибровки.

**Files:**
- Create: `backend/config/synth_config.yaml`, `backend/app/synth/config.py`, `backend/app/synth/requests.py`, `backend/app/synth/engineers.py`, `backend/app/synth/events.py`
- Test: `backend/tests/test_synth.py`

**Interfaces:**
- Consumes: `RawFile`, `RawRequestRow` из Task 3, `GeoResult` из Task 5, модели из Task 2.
- Produces:
  - `SynthConfig.load(path) -> SynthConfig` с полями `seed`, `event_time`, `regions: dict[str, RegionConfig(title, control, synthetic)]`, `skill_by_bk`, `default_duration_min`, `duration_jitter`, `duration_round_to`, `duration_by_hd`, `urgent_bk_types`, `urgent_control_statuses`, `cancelled_control_statuses`, `transport_required_rules: list[TransportRule(transport, skill, hd_contains)]`, `transport_mix`, `force_car_for_skills`, `transport_from_history: bool`, `engineer_start: "office" | "history_medoid"`, `shifts: list[ShiftTemplate(start, end)]`, `urgent_event: UrgentEventConfig(duration_min, window_min)`.
  - `synth_duration(cfg, request_id, type_hd) -> int`, `synth_transport_required(cfg, skill, type_hd) -> Transport | None`, `check_alignment(synthetic: RawFile, control: RawFile) -> None` (ValueError при расхождении), `build_requests(cfg, synthetic, control | None, geocode: Callable[[address, district], GeoResult]) -> list[Request]`.
  - `crew_histories(control) -> dict[crew, list[RawRequestRow]]`, `choose_shift(cfg, rows) -> ShiftTemplate`, `largest_remainder(shares, total) -> dict[Transport, int]`, `historical_car_crews(cfg, histories) -> set[crew]` (бригады, чьи реальные заявки по правилам конфига требовали автомобиль), `assign_transports(cfg, region, engineers: list[tuple[id, set[Skill]]], forced_car_ids: set[id] = frozenset()) -> dict[id, Transport]`, `history_medoid(rows, row_points: dict[row_index, (lat, lon)]) -> (lat, lon) | None`, `build_engineers(cfg, region, control, office, row_points=None) -> (list[Engineer], dict[crew_name, engineer_id])`. ID инженеров `E01`, `E02`… по алфавиту названий бригад. Старт инженера: офис или медоид истории бригады по `cfg.engineer_start`.
  - `URGENT_REQUEST_ID = "URG-001"`, `build_demo_events(cfg, region, requests, control, synthetic, crew_to_engineer, plan: Plan | None = None) -> list[Event]` в порядке: отмена, недоступность инженера, срочная заявка. С планом отменяется отменённая в контроле заявка, стоящая в плане после времени события, а недоступным становится инженер с наибольшим числом визитов после этого времени, иначе события заметно не меняют план. Без плана берётся самая загруженная бригада по истории.

- [ ] **Step 1: Создать конфиг**

`backend/config/synth_config.yaml`:

```yaml
# Правила досинтеза данных, которых нет в выгрузке Билайна. Любое изменение здесь
# меняет бандлы: перезапустите prepare и закоммитьте data/bundles.
#
# Калибровка (2026-09-15): реальное распределение диспетчеров в основном было выполнено,
# поэтому параметры подобраны так, чтобы план диспетчеров был почти допустим в нашей модели.
# Сетка «длительности x смены x старт x транспорт из истории» на трёх регионах дала лучший
# результат при длительностях x0.75 от первой оценки, одной смене 10:00-22:00 (график 2/2),
# старте из медоида истории бригады и автомобиле у бригад, которым он был нужен по истории.
seed: 20260817
event_time: "13:00"

regions:
  east:
    title: Восток
    control: data/raw/east_control.csv
    synthetic: data/raw/east_synthetic.csv
  south_east:
    title: Юго-восток
    control: data/raw/south_east_control.csv
    synthetic: data/raw/south_east_synthetic.csv
  south_center:
    title: Югоцентр
    control: data/raw/south_center_control.csv
    synthetic: data/raw/south_center_synthetic.csv

# Тип заявки BK -> навык из справочника ТЗ (п. 2.4.1)
skill_by_bk:
  Локальная заявка: local
  Подключение: connection
  Дозаказ: connection
  Глобальная проблема: emergency

# Длительность работ по типу заявки HD, минуты (медиана), разброс ±duration_jitter
default_duration_min: 35
duration_jitter: 0.15
duration_round_to: 5
duration_by_hd:
  Конвергенция абонента: 45
  Заявка на подключение: 70
  Заказ подключения/Дозаказ оборудования: 45
  Дозаказ оборудования: 20
  Нет линка: 35
  Авария: 70
  Информация: 15
  Мониторинг: 15
  Низкая скорость: 30
  Рост ошибок на порту: 30
  IP-адрес 169...: 20
  Переключение на Гбит/с: 35
  Работа с кабелем: 45
  Разрывы: 35
  Роутер. Замена техническим специалистом: 20
  TVE/ENT. Другие ошибки: 20
  TVE/ENT. Замена приставки техником: 20
  ТВ. Замена приставки техником: 20

# Приоритет «Срочная»
urgent_bk_types: [Глобальная проблема]
urgent_control_statuses: [Просрочена]
cancelled_control_statuses: [Отменена]

# Требуемый транспорт в заявке: первое сработавшее правило
transport_required_rules:
  - {skill: emergency, transport: car}
  - {hd_contains: Дозаказ оборудования, transport: car}

# Доли типов транспорта у инженеров; при 4+ инженерах каждый тип встречается хотя бы раз
transport_mix: {car: 0.55, public: 0.2, foot: 0.15, bike: 0.1}
force_car_for_skills: [emergency]
# Бригада, чьи реальные заявки требовали автомобиль по правилам выше, получает автомобиль
transport_from_history: true

# Стартовая точка: office или history_medoid (адрес из истории бригады, ближайший ко всем остальным).
# Бригады Каширы и Ступино в реальности не выезжают из московского офиса.
engineer_start: history_medoid

# Шаблоны смен: инженеру достаётся тот, что покрывает больше его исторических заявок
shifts:
  - {start: "10:00", end: "22:00"}

urgent_event:
  duration_min: 60
  window_min: 120
```

- [ ] **Step 2: Написать падающий тест**

`backend/tests/test_synth.py`:

```python
from pathlib import Path

import pytest

from app.domain.enums import EventType, Priority, Skill, Transport
from app.domain.models import Metrics, Office, Plan, Route, Visit
from app.ingest.beeline_csv import RawFile, RawRequestRow
from app.ingest.geocode import GeoResult
from app.synth.config import ShiftTemplate, SynthConfig
from app.synth.engineers import (
    assign_transports,
    build_engineers,
    choose_shift,
    crew_histories,
    historical_car_crews,
    history_medoid,
    largest_remainder,
)
from app.synth.events import build_demo_events
from app.synth.requests import build_requests, check_alignment, synth_duration, synth_transport_required

CONFIG = Path(__file__).resolve().parents[1] / "config" / "synth_config.yaml"


@pytest.fixture(scope="module")
def cfg():
    return SynthConfig.load(CONFIG)


def row(
    index,
    rid,
    type_bk="Локальная заявка",
    type_hd="Нет линка",
    ws=600,
    we=720,
    crew="",
    status="",
    address="Город Москва, ул.Тестовая, д. 1",
):
    return RawRequestRow(
        row_index=index,
        request_id=rid,
        type_bk=type_bk,
        type_hd=type_hd,
        window_start=ws,
        window_end=we,
        district="Таганский",
        address=address,
        status_bk=status,
        crew=crew,
    )


def test_config_loads_all_regions(cfg):
    assert set(cfg.regions) == {"east", "south_east", "south_center"}
    assert cfg.skill_by_bk["Дозаказ"] == Skill.CONNECTION


def test_duration_is_deterministic_rounded_and_within_jitter(cfg):
    base = cfg.duration_by_hd["Авария"]
    first = synth_duration(cfg, "74198", "Авария")
    assert first == synth_duration(cfg, "74198", "Авария")
    assert first % cfg.duration_round_to == 0
    assert (
        base * (1 - cfg.duration_jitter) - cfg.duration_round_to
        <= first
        <= base * (1 + cfg.duration_jitter) + cfg.duration_round_to
    )
    default = synth_duration(cfg, "1", "Неизвестный тип")
    assert (
        abs(default - cfg.default_duration_min)
        <= cfg.default_duration_min * cfg.duration_jitter + cfg.duration_round_to
    )


def test_transport_rules(cfg):
    assert synth_transport_required(cfg, Skill.EMERGENCY, "Авария") == Transport.CAR
    assert (
        synth_transport_required(cfg, Skill.CONNECTION, "Заказ подключения/Дозаказ оборудования")
        == Transport.CAR
    )
    assert synth_transport_required(cfg, Skill.LOCAL, "Нет линка") is None


def test_largest_remainder_sums_to_total(cfg):
    counts = largest_remainder(cfg.transport_mix, 12)
    assert sum(counts.values()) == 12


def test_assign_transports_covers_all_types_and_gives_cars_to_emergency(cfg):
    engineers = [(f"E{k:02d}", {Skill.EMERGENCY} if k < 4 else {Skill.LOCAL}) for k in range(12)]
    result = assign_transports(cfg, "east", engineers)
    assert set(result.values()) == set(Transport)
    assert all(result[f"E{k:02d}"] == Transport.CAR for k in range(4))
    assert result == assign_transports(cfg, "east", engineers)


def test_choose_shift_prefers_coverage(cfg):
    two_shifts = cfg.model_copy(
        update={
            "shifts": [ShiftTemplate(start="09:00", end="18:00"), ShiftTemplate(start="13:00", end="22:00")]
        }
    )
    evening = [row(0, "a", ws=1080, we=1200), row(1, "b", ws=1200, we=1320)]
    morning = [row(0, "a", ws=600, we=720)]
    chosen = choose_shift(two_shifts, evening)
    assert (chosen.start, chosen.end) == (780, 1320)
    assert choose_shift(two_shifts, morning).start == 540


def test_check_alignment_detects_mismatch():
    synthetic = RawFile(rows=[row(0, "1", ws=600)], office_address="x", is_control=False)
    control = RawFile(rows=[row(0, "305", ws=720)], office_address=None, is_control=True)
    with pytest.raises(ValueError, match="не согласованы"):
        check_alignment(synthetic, control)


def _fake_geo(address, district):
    return GeoResult(55.75, 37.62, "house", address)


def test_build_requests_marks_urgent_from_type_and_control_status(cfg):
    synthetic = RawFile(
        rows=[row(0, "1"), row(1, "2", type_bk="Глобальная проблема", type_hd="Авария", ws=1, we=1439)],
        office_address="x",
        is_control=False,
    )
    control = RawFile(
        rows=[
            row(0, "305", status="Просрочена"),
            row(1, "306", type_bk="Глобальная проблема", type_hd="Авария", ws=1, we=1439),
        ],
        office_address=None,
        is_control=True,
    )
    requests = build_requests(cfg, synthetic, control, _fake_geo)
    assert [r.priority for r in requests] == [Priority.URGENT, Priority.URGENT]
    assert requests[1].skill == Skill.EMERGENCY and requests[1].transport_required == Transport.CAR


def test_build_requests_rejects_unknown_bk_type(cfg):
    synthetic = RawFile(rows=[row(0, "1", type_bk="Непонятно")], office_address="x", is_control=False)
    with pytest.raises(ValueError, match="Неизвестный тип"):
        build_requests(cfg, synthetic, None, _fake_geo)


def test_build_engineers_from_crew_history(cfg):
    control = RawFile(
        rows=[
            row(0, "1", crew="Бригада Б"),
            row(1, "2", type_bk="Подключение", crew="Бригада А"),
            row(2, "3", type_bk="Глобальная проблема", crew="Бригада А"),
            row(3, "4", crew=""),
        ],
        office_address=None,
        is_control=True,
    )
    office = Office(region="east", title="Восток", address="x", lat=55.7, lon=37.7)
    engineers, mapping = build_engineers(cfg, "east", control, office)
    assert mapping == {"Бригада А": "E01", "Бригада Б": "E02"}
    assert engineers[0].skills == [Skill.CONNECTION, Skill.EMERGENCY]
    assert engineers[0].transport == Transport.CAR
    assert (engineers[1].start_lat, engineers[1].start_lon) == (55.7, 37.7)


def test_demo_events_cover_three_event_types(cfg):
    synthetic = RawFile(
        rows=[row(0, "1", ws=900, we=1020), row(1, "2")], office_address="x", is_control=False
    )
    control = RawFile(
        rows=[
            row(0, "305", status="Отменена", crew="Бригада А", ws=900, we=1020),
            row(1, "306", crew="Бригада А"),
        ],
        office_address=None,
        is_control=True,
    )
    requests = build_requests(cfg, synthetic, control, _fake_geo)
    events = build_demo_events(cfg, "east", requests, control, synthetic, {"Бригада А": "E01"})
    assert [e.type for e in events] == [EventType.CANCEL, EventType.ENGINEER_UNAVAILABLE, EventType.URGENT]
    assert events[0].request_id == "1"
    assert events[2].request.window_start == 780 and events[2].request.priority == Priority.URGENT


def test_transport_from_history_gives_cars_to_crews_that_carried_equipment(cfg):
    control = RawFile(
        rows=[
            row(0, "1", type_bk="Дозаказ", type_hd="Дозаказ оборудования", crew="Бригада А"),
            row(1, "2", crew="Бригада Б"),
            row(2, "3", crew="Бригада В"),
            row(3, "4", crew="Бригада Г"),
            row(4, "5", crew="Бригада Д"),
        ],
        office_address=None,
        is_control=True,
    )
    office = Office(region="east", title="Восток", address="x", lat=55.7, lon=37.7)
    history_cfg = cfg.model_copy(update={"transport_from_history": True})
    assert historical_car_crews(history_cfg, crew_histories(control)) == {"Бригада А"}
    engineers, _ = build_engineers(history_cfg, "east", control, office)
    by_name = {engineer.name: engineer for engineer in engineers}
    assert by_name["Бригада А"].transport == Transport.CAR
    assert set(engineer.transport for engineer in engineers) == set(Transport)


def test_history_medoid_picks_the_most_central_real_address():
    rows = [row(0, "1"), row(1, "2"), row(2, "3")]
    points = {0: (55.70, 37.60), 1: (55.709, 37.60), 2: (55.79, 37.60)}
    assert history_medoid(rows, points) == (55.709, 37.60)
    assert history_medoid(rows, {}) is None


def test_engineer_start_from_history_medoid(cfg):
    control = RawFile(
        rows=[row(0, "1", crew="Бригада А"), row(1, "2", crew="Бригада А")],
        office_address=None,
        is_control=True,
    )
    office = Office(region="east", title="Восток", address="x", lat=55.7, lon=37.7)
    medoid_cfg = cfg.model_copy(update={"engineer_start": "history_medoid"})
    engineers, _ = build_engineers(
        medoid_cfg, "east", control, office, {0: (54.83, 38.15), 1: (54.84, 38.16)}
    )
    assert (engineers[0].start_lat, engineers[0].start_lon) in {(54.83, 38.15), (54.84, 38.16)}
    office_cfg = cfg.model_copy(update={"engineer_start": "office"})
    office_engineers, _ = build_engineers(office_cfg, "east", control, office, {0: (54.83, 38.15)})
    assert (office_engineers[0].start_lat, office_engineers[0].start_lon) == (55.7, 37.7)


def test_demo_events_follow_the_optimized_plan(cfg):
    synthetic = RawFile(
        rows=[row(0, "1", ws=900, we=1020), row(1, "2", ws=900, we=1020), row(2, "3")],
        office_address="x",
        is_control=False,
    )
    control = RawFile(
        rows=[
            row(0, "305", status="Отменена", crew="Бригада А", ws=900, we=1020),
            row(1, "306", status="Отменена", crew="Бригада А", ws=900, we=1020),
            row(2, "307", crew="Бригада А"),
        ],
        office_address=None,
        is_control=True,
    )
    requests = build_requests(cfg, synthetic, control, _fake_geo)

    def visit(request_id, start):
        return Visit(request_id=request_id, arrival=start, start=start, end=start + 30, leg_km=1.0, leg_min=5)

    plan = Plan(
        solver="ortools",
        routes=[
            Route(engineer_id="E01", visits=[visit("3", 600)]),
            Route(engineer_id="E02", visits=[visit("2", 900)]),
        ],
        unassigned=[],
        metrics=Metrics(engineers_used=2, km_per_engineer={}, total_km=2.0, assigned=2, unassigned=0),
    )
    events = build_demo_events(cfg, "east", requests, control, synthetic, {"Бригада А": "E01"}, plan)
    assert events[0].request_id == "2"
    assert events[1].engineer_id == "E02"
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_synth.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.synth.config'`.

- [ ] **Step 4: Реализовать `config.py`**

`backend/app/synth/config.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

from app.domain.enums import Skill, Transport
from app.domain.timeutil import HHMM


class RegionConfig(BaseModel):
    title: str
    control: str
    synthetic: str


class TransportRule(BaseModel):
    transport: Transport
    skill: Skill | None = None
    hd_contains: str | None = None

    @model_validator(mode="after")
    def _has_condition(self) -> TransportRule:
        if self.skill is None and self.hd_contains is None:
            raise ValueError("правило транспорта должно иметь skill или hd_contains")
        return self


class ShiftTemplate(BaseModel):
    start: HHMM
    end: HHMM


class UrgentEventConfig(BaseModel):
    duration_min: int
    window_min: int


class SynthConfig(BaseModel):
    seed: int
    event_time: HHMM
    regions: dict[str, RegionConfig]
    skill_by_bk: dict[str, Skill]
    default_duration_min: int
    duration_jitter: float
    duration_round_to: int
    duration_by_hd: dict[str, int]
    urgent_bk_types: list[str]
    urgent_control_statuses: list[str]
    cancelled_control_statuses: list[str]
    transport_required_rules: list[TransportRule]
    transport_mix: dict[Transport, float]
    force_car_for_skills: list[Skill]
    transport_from_history: bool = False
    engineer_start: Literal["office", "history_medoid"] = "office"
    shifts: list[ShiftTemplate]
    urgent_event: UrgentEventConfig

    @classmethod
    def load(cls, path: Path) -> SynthConfig:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
```

- [ ] **Step 5: Реализовать `requests.py`**

`backend/app/synth/requests.py`:

```python
"""Заявки из синтетического файла + досинтез длительности, приоритета и транспорта."""

from __future__ import annotations

import random
from collections.abc import Callable

from app.domain.enums import Priority, Skill, Transport
from app.domain.models import Request
from app.ingest.beeline_csv import RawFile
from app.ingest.geocode import GeoResult
from app.synth.config import SynthConfig


def synth_duration(cfg: SynthConfig, request_id: str, type_hd: str) -> int:
    base = cfg.duration_by_hd.get(type_hd, cfg.default_duration_min)
    rng = random.Random(f"{cfg.seed}:duration:{request_id}")
    value = base * (1 + rng.uniform(-cfg.duration_jitter, cfg.duration_jitter))
    step = cfg.duration_round_to
    return max(step, round(value / step) * step)


def synth_transport_required(cfg: SynthConfig, skill: Skill, type_hd: str) -> Transport | None:
    for rule in cfg.transport_required_rules:
        if rule.skill is not None and rule.skill != skill:
            continue
        if rule.hd_contains is not None and rule.hd_contains not in type_hd:
            continue
        return rule.transport
    return None


def check_alignment(synthetic: RawFile, control: RawFile) -> None:
    """Синтетический и контрольный файлы описывают одни и те же заявки построчно."""
    problems: list[str] = []
    if len(synthetic.rows) != len(control.rows):
        problems.append(f"разное число заявок: {len(synthetic.rows)} и {len(control.rows)}")
    for s_row, c_row in zip(synthetic.rows, control.rows, strict=False):
        same = (
            s_row.window_start == c_row.window_start
            and s_row.window_end == c_row.window_end
            and s_row.type_hd == c_row.type_hd
            and c_row.address.startswith(s_row.address)
        )
        if not same:
            problems.append(
                f"строка {s_row.row_index + 1}: {s_row.request_id} не совпадает с {c_row.request_id}"
            )
    ids = [row.request_id for row in synthetic.rows]
    duplicates = sorted({request_id for request_id in ids if ids.count(request_id) > 1})
    if duplicates:
        problems.append(f"повторяются номера заявок: {', '.join(duplicates)}")
    if problems:
        raise ValueError("Файлы региона не согласованы: " + "; ".join(problems[:10]))


def build_requests(
    cfg: SynthConfig,
    synthetic: RawFile,
    control: RawFile | None,
    geocode: Callable[[str, str], GeoResult],
) -> list[Request]:
    statuses = [row.status_bk for row in control.rows] if control is not None else None
    requests: list[Request] = []
    for row in synthetic.rows:
        skill = cfg.skill_by_bk.get(row.type_bk)
        if skill is None:
            raise ValueError(f"Неизвестный тип заявки BK «{row.type_bk}» (заявка {row.request_id})")
        status = statuses[row.row_index] if statuses is not None else ""
        urgent = row.type_bk in cfg.urgent_bk_types or status in cfg.urgent_control_statuses
        geo = geocode(row.address, row.district)
        requests.append(
            Request(
                id=row.request_id,
                address=row.address,
                lat=geo.lat,
                lon=geo.lon,
                geocode_precision=geo.precision,
                district=row.district,
                duration_min=synth_duration(cfg, row.request_id, row.type_hd),
                window_start=row.window_start,
                window_end=row.window_end,
                priority=Priority.URGENT if urgent else Priority.NORMAL,
                skill=skill,
                transport_required=synth_transport_required(cfg, skill, row.type_hd),
                source_type_bk=row.type_bk,
                source_type_hd=row.type_hd,
            )
        )
    return requests
```

- [ ] **Step 6: Реализовать `engineers.py`**

`backend/app/synth/engineers.py`:

```python
"""Инженеры: по одному на бригаду из контрольного распределения."""

from __future__ import annotations

import random
from collections import defaultdict

from app.domain.enums import Skill, Transport
from app.domain.models import Engineer, Office
from app.geo.haversine import haversine_km
from app.ingest.beeline_csv import RawFile, RawRequestRow
from app.synth.config import ShiftTemplate, SynthConfig
from app.synth.requests import synth_transport_required

SHIFT_TAIL_MIN = 30  # заявка «покрыта» сменой, если в окне остаётся хотя бы полчаса смены


def crew_histories(control: RawFile) -> dict[str, list[RawRequestRow]]:
    histories: dict[str, list[RawRequestRow]] = defaultdict(list)
    for row in control.rows:
        if row.crew:
            histories[row.crew].append(row)
    return dict(histories)


def choose_shift(cfg: SynthConfig, rows: list[RawRequestRow]) -> ShiftTemplate:
    def covered(shift: ShiftTemplate) -> int:
        return sum(
            1
            for row in rows
            if max(row.window_start, shift.start) <= min(row.window_end, shift.end - SHIFT_TAIL_MIN)
        )

    return max(cfg.shifts, key=covered)  # max возвращает первый из равных


def largest_remainder(shares: dict[Transport, float], total: int) -> dict[Transport, int]:
    weight = sum(shares.values())
    raw = {t: shares.get(t, 0.0) / weight * total for t in Transport}
    counts = {t: int(raw[t]) for t in Transport}
    left = total - sum(counts.values())
    for t in sorted(Transport, key=lambda item: raw[item] - counts[item], reverse=True)[:left]:
        counts[t] += 1
    return counts


def historical_car_crews(cfg: SynthConfig, histories: dict[str, list[RawRequestRow]]) -> set[str]:
    """Бригады, чьи реальные заявки по правилам конфига требовали автомобиль."""
    return {
        name
        for name, rows in histories.items()
        if any(
            synth_transport_required(cfg, cfg.skill_by_bk[row.type_bk], row.type_hd) == Transport.CAR
            for row in rows
        )
    }


def assign_transports(
    cfg: SynthConfig,
    region: str,
    engineers: list[tuple[str, set[Skill]]],
    forced_car_ids: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Transport]:
    total = len(engineers)
    counts = largest_remainder(cfg.transport_mix, total)
    if total >= len(Transport):
        for t in Transport:
            while counts[t] == 0:
                donor = max(counts, key=counts.get)
                counts[donor] -= 1
                counts[t] += 1
    forced = [
        eid for eid, skills in engineers if skills & set(cfg.force_car_for_skills) or eid in forced_car_ids
    ]
    deficit = len(forced) - counts[Transport.CAR]
    for t in sorted((t for t in Transport if t != Transport.CAR), key=counts.get, reverse=True):
        while deficit > 0 and counts[t] > 1:
            counts[t] -= 1
            counts[Transport.CAR] += 1
            deficit -= 1
    if deficit > 0:
        raise ValueError(f"Регион {region}: не хватает автомобилей для бригад, которым он обязателен")
    pool = [t for t in Transport for _ in range(counts[t])]
    for _ in forced:
        pool.remove(Transport.CAR)
    random.Random(f"{cfg.seed}:transport:{region}").shuffle(pool)
    rest = [eid for eid, _ in engineers if eid not in forced]
    result = {eid: Transport.CAR for eid in forced}
    result.update(zip(rest, pool, strict=True))
    return result


def history_medoid(
    rows: list[RawRequestRow], row_points: dict[int, tuple[float, float]]
) -> tuple[float, float] | None:
    """Адрес из истории бригады с минимальной суммой расстояний до остальных её адресов."""
    points = [row_points[row.row_index] for row in rows if row.row_index in row_points]
    if not points:
        return None
    return min(points, key=lambda p: sum(haversine_km(*p, *q) for q in points))


def build_engineers(
    cfg: SynthConfig,
    region: str,
    control: RawFile,
    office: Office,
    row_points: dict[int, tuple[float, float]] | None = None,
) -> tuple[list[Engineer], dict[str, str]]:
    histories = crew_histories(control)
    names = sorted(histories)
    ids = {name: f"E{k + 1:02d}" for k, name in enumerate(names)}
    order = list(Skill)
    skills = {
        name: sorted({cfg.skill_by_bk[row.type_bk] for row in histories[name]}, key=order.index)
        for name in names
    }
    forced_car_ids = (
        {ids[name] for name in historical_car_crews(cfg, histories)} if cfg.transport_from_history else set()
    )
    transports = assign_transports(
        cfg, region, [(ids[name], set(skills[name])) for name in names], forced_car_ids
    )
    engineers = []
    for name in names:
        shift = choose_shift(cfg, histories[name])
        start = (office.lat, office.lon)
        if cfg.engineer_start == "history_medoid" and row_points:
            start = history_medoid(histories[name], row_points) or start
        engineers.append(
            Engineer(
                id=ids[name],
                name=name,
                start_lat=start[0],
                start_lon=start[1],
                shift_start=shift.start,
                shift_end=shift.end,
                skills=skills[name],
                transport=transports[ids[name]],
            )
        )
    return engineers, ids
```

- [ ] **Step 7: Реализовать `events.py`**

`backend/app/synth/events.py`:

```python
"""Готовые события для демо: отмена, недоступность инженера, срочная заявка.

Если передан оптимизированный план, события выбираются так, чтобы менять его заметно:
отменяется заявка, стоящая в плане после времени события, недоступным становится инженер
с наибольшим числом визитов после этого времени.
"""

from __future__ import annotations

import random
from collections import Counter

from app.domain.enums import EventType, Priority, Skill, Transport
from app.domain.models import Event, Plan, Request
from app.domain.timeutil import DAY_MIN
from app.ingest.beeline_csv import RawFile
from app.synth.config import SynthConfig

URGENT_REQUEST_ID = "URG-001"


def build_demo_events(
    cfg: SynthConfig,
    region: str,
    requests: list[Request],
    control: RawFile,
    synthetic: RawFile,
    crew_to_engineer: dict[str, str],
    plan: Plan | None = None,
) -> list[Event]:
    at = cfg.event_time
    located = {r.id: r for r in requests if r.lat is not None}
    events: list[Event] = []

    cancelled = [
        s.request_id
        for c, s in zip(control.rows, synthetic.rows, strict=True)
        if c.status_bk in cfg.cancelled_control_statuses and s.request_id in located
    ]
    planned_start = {v.request_id: v.start for route in plan.routes for v in route.visits} if plan else {}
    planned_later = [rid for rid in cancelled if planned_start.get(rid, -1) >= at]
    later = [rid for rid in cancelled if located[rid].window_start >= at]
    candidates = planned_later or later or cancelled
    if candidates:
        events.append(Event(type=EventType.CANCEL, time=at, request_id=candidates[0]))

    engineer_id = _busiest_in_plan(plan, at) if plan is not None else None
    if engineer_id is None:
        crews = Counter(row.crew for row in control.rows if row.crew in crew_to_engineer)
        if crews:
            engineer_id = crew_to_engineer[min(crews.items(), key=lambda item: (-item[1], item[0]))[0]]
    if engineer_id is not None:
        events.append(Event(type=EventType.ENGINEER_UNAVAILABLE, time=at, engineer_id=engineer_id))

    if located:
        base = random.Random(f"{cfg.seed}:urgent:{region}").choice(
            sorted(located.values(), key=lambda r: r.id)
        )
        events.append(
            Event(
                type=EventType.URGENT,
                time=at,
                request=Request(
                    id=URGENT_REQUEST_ID,
                    address=base.address,
                    lat=base.lat,
                    lon=base.lon,
                    geocode_precision=base.geocode_precision,
                    district=base.district,
                    duration_min=cfg.urgent_event.duration_min,
                    window_start=at,
                    window_end=min(DAY_MIN - 1, at + cfg.urgent_event.window_min),
                    priority=Priority.URGENT,
                    skill=Skill.EMERGENCY,
                    transport_required=Transport.CAR,
                    source_type_bk="Глобальная проблема",
                    source_type_hd="Авария",
                ),
            )
        )
    return events


def _busiest_in_plan(plan: Plan, at: int) -> str | None:
    counts = {route.engineer_id: sum(1 for v in route.visits if v.start >= at) for route in plan.routes}
    if not counts:
        return None
    engineer_id, count = min(counts.items(), key=lambda item: (-item[1], item[0]))
    return engineer_id if count > 0 else None
```

- [ ] **Step 8: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_synth.py`
Expected: `15 passed`

- [ ] **Step 9: Commit**

```bash
git add backend/config/synth_config.yaml backend/app/synth/config.py backend/app/synth/requests.py backend/app/synth/engineers.py backend/app/synth/events.py backend/tests/test_synth.py
git commit -m "feat(synth): config-driven synthesis of engineers, durations, priorities and demo events" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 12: План диспетчеров и CLI `prepare`

**Files:**
- Create: `backend/app/synth/control.py`, `backend/app/synth/prepare.py`
- Test: `backend/tests/test_prepare.py`

**Interfaces:**
- Consumes: всё из Tasks 2-11.
- Produces:
  - `CONTROL_SOLVER = "dispatchers"`, `build_control_plan(problem, control, synthetic, crew_to_engineer) -> Plan`: заявки бригады в порядке окон, время и пробег по нашей модели, нарушения считаются, заявки без бригады неназначены с текстом «Диспетчер не назначил бригаду.».
  - Демо-события строятся по оптимизированному плану: `build_demo_events(..., optimized)`.
  - `PrepareResult(bundle, fcfs, optimized, report, self_check_ok)`, `lexicographic(metrics) -> (unassigned, engineers_used, total_km)`, `self_check(fcfs, optimized) -> (bool, str)`, `render_report(...) -> str`, `prepare_region(region, cfg, *, repo_root, geocoder, osrm, cache, time_limit_s, traffic) -> PrepareResult`, `main(argv=None) -> int`.
  - CLI: `uv run python -m app.synth.prepare [--region all|east|south_east|south_center] [--osrm-url URL] [--geocoder nominatim|cache-only] [--time-limit N]`. Пишет `data/bundles/<region>/bundle.json` и `report.md`, обновляет `data/geocode_cache.json`, кэш матриц в `data/cache.sqlite`. Код выхода 1, если самопроверка не прошла хотя бы в одном регионе.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/test_prepare.py`:

```python
import hashlib
import json

from app.domain.models import Metrics
from app.geo.matrix import TrafficProfile
from app.ingest.geocode import GeoHit
from app.synth.config import SynthConfig
from app.synth.prepare import prepare_region, self_check
from tests.test_synth import CONFIG

SYNTHETIC = (
    "Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Гигабитное подключение\r\n"
    "11;Локальная заявка;Нет линка;17.08.2026 14:00;17.08.2026 16:00;Таганский;Город Москва, ул.Первая, д. 1;Нет\r\n"
    "12;Локальная заявка;Нет линка;17.08.2026 10:00;17.08.2026 12:00;Таганский;Город Москва, ул.Вторая, д. 2;Нет\r\n"
    ";;;;;;;\r\n"
    "Адрес Офиса;г. Москва, ул Юных Ленинцев, д 83с 4;;;;;;\r\n"
)
CONTROL = (
    "Заявка;Тип заявки BK;Статус BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Бригада;Гигабитное подключение\r\n"
    "305;Локальная заявка;Выполнена;Нет линка;17.08.2026 14:00;17.08.2026 16:00;Таганский;"
    "Город Москва, ул.Первая, д. 1, кв. 5;Бригада А;Нет\r\n"
    "306;Локальная заявка;Отменена;Нет линка;17.08.2026 10:00;17.08.2026 12:00;Таганский;"
    "Город Москва, ул.Вторая, д. 2, кв. 7;Бригада Б;Нет\r\n"
)


class HashGeocoder:
    """Детерминированные точки в пределах ~3 км от центра Москвы."""

    def lookup(self, query):
        digest = hashlib.sha256(query.encode()).digest()
        return GeoHit(55.74 + digest[0] / 255 * 0.03, 37.60 + digest[1] / 255 * 0.05, "building")


def test_prepare_region_end_to_end(tmp_path):
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "t_synthetic.csv").write_bytes(SYNTHETIC.encode("cp1251"))
    (tmp_path / "data" / "raw" / "t_control.csv").write_bytes(CONTROL.encode("utf-8"))
    cfg = SynthConfig.load(CONFIG).model_copy(deep=True)
    cfg.regions = {
        "t": cfg.regions["east"].model_copy(
            update={"control": "data/raw/t_control.csv", "synthetic": "data/raw/t_synthetic.csv"}
        )
    }

    result = prepare_region(
        "t",
        cfg,
        repo_root=tmp_path,
        geocoder=HashGeocoder(),
        osrm=None,
        cache=None,
        time_limit_s=1,
        traffic=TrafficProfile({}),
    )

    assert result.fcfs.metrics.engineers_used == 2
    assert result.optimized.metrics.engineers_used == 1
    assert result.self_check_ok
    assert len(result.bundle.engineers) == 2 and len(result.bundle.requests) == 2
    assert result.bundle.control_plan.metrics.engineers_used == 2
    assert "| Оптимизированный (OR-Tools) | 1 |" in result.report
    cache = json.loads((tmp_path / "data" / "geocode_cache.json").read_text(encoding="utf-8"))
    assert "Москва, Юных Ленинцев улица, 83с4" in cache


def test_self_check_requires_strict_improvement():
    worse = Metrics(engineers_used=3, km_per_engineer={}, total_km=10, assigned=5, unassigned=0)
    better = Metrics(engineers_used=2, km_per_engineer={}, total_km=12, assigned=5, unassigned=0)
    assert self_check(worse, better)[0]
    assert not self_check(better, better)[0]
    assert not self_check(better, worse)[0]
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_prepare.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.synth.prepare'`.

- [ ] **Step 3: Реализовать `control.py`**

`backend/app/synth/control.py`:

```python
"""План диспетчеров из контрольного распределения, посчитанный нашей моделью времени и пробега."""

from __future__ import annotations

from app.domain.enums import ReasonCode
from app.domain.models import Plan, Unassigned
from app.ingest.beeline_csv import RawFile
from app.solvers.assemble import build_plan
from app.solvers.problem import Problem

CONTROL_SOLVER = "dispatchers"


def build_control_plan(
    problem: Problem, control: RawFile, synthetic: RawFile, crew_to_engineer: dict[str, str]
) -> Plan:
    sequences: dict[str, list[str]] = {state.engineer.id: [] for state in problem.states}
    fixed: dict[str, Unassigned] = {}
    for c_row, s_row in zip(control.rows, synthetic.rows, strict=True):
        request_id = s_row.request_id
        if not problem.has_request(request_id):
            continue
        engineer_id = crew_to_engineer.get(c_row.crew)
        if engineer_id is None:
            fixed[request_id] = Unassigned(
                request_id=request_id,
                reason_code=ReasonCode.NO_FREE_ENGINEER,
                reason_text="Диспетчер не назначил бригаду.",
            )
            continue
        sequences[engineer_id].append(request_id)
    for sequence in sequences.values():
        sequence.sort(key=lambda rid: (problem.request(rid).window_start, problem.request(rid).window_end))
    return build_plan(problem, CONTROL_SOLVER, sequences, fixed_unassigned=fixed)
```

- [ ] **Step 4: Реализовать `prepare.py`**

`backend/app/synth/prepare.py`:

```python
"""CLI: сырые CSV региона -> data/bundles/<region>/bundle.json + report.md.

Запуск из каталога backend:  uv run python -m app.synth.prepare --region all
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.domain.models import Bundle, Metrics, Office, Plan
from app.geo.kvcache import KVCache
from app.geo.matrix import TrafficProfile, TravelModel
from app.geo.osrm import OsrmClient
from app.ingest.beeline_csv import RawFile, parse_beeline_csv
from app.ingest.bundle import save_bundle
from app.ingest.geocode import Geocoder, JsonGeocodeCache, NominatimGeocoder, geocode_address
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import OrToolsSolver
from app.solvers.problem import make_problem
from app.synth.config import SynthConfig
from app.synth.control import build_control_plan
from app.synth.engineers import build_engineers
from app.synth.events import build_demo_events
from app.synth.requests import build_requests, check_alignment

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


@dataclass
class PrepareResult:
    bundle: Bundle
    fcfs: Plan
    optimized: Plan
    report: str
    self_check_ok: bool


def lexicographic(metrics: Metrics) -> tuple[int, int, float]:
    return metrics.unassigned, metrics.engineers_used, round(metrics.total_km, 2)


def self_check(fcfs: Metrics, optimized: Metrics) -> tuple[bool, str]:
    base, best = lexicographic(fcfs), lexicographic(optimized)
    if best > base:
        return False, f"Оптимизированный план хуже базового: {best} против {base}"
    if best == base:
        return False, "Базовый вариант не уступает оптимизированному: в данных нет конфликта из ТЗ"
    return True, "Базовый вариант уступает оптимизированному хотя бы по одной обязательной метрике"


def render_report(
    title: str,
    region: str,
    synthetic: RawFile,
    bundle: Bundle,
    source: str,
    plans: list[tuple[str, Plan]],
    check: tuple[bool, str],
) -> str:
    precision = Counter(request.geocode_precision for request in bundle.requests)
    lines = [
        f"# Регион {title} ({region})",
        "",
        "## Данные",
        "",
        "| Показатель | Значение |",
        "|---|---|",
        f"| Заявок | {len(bundle.requests)} |",
        f"| Инженеров | {len(bundle.engineers)} |",
        f"| Отброшено строк | {len(synthetic.skipped)} |",
        f"| Источник матрицы | {source} |",
        (
            f"| Геокодирование: дом / улица / район / не найдено | {precision['house']} / "
            f"{precision['street']} / {precision['locality']} / {precision['none']} |"
        ),
        "",
        "## Сравнение планов",
        "",
        "| План | Инженеров | Км | Назначено | Не назначено | Нарушений |",
        "|---|---|---|---|---|---|",
    ]
    for label, plan in plans:
        m = plan.metrics
        lines.append(
            f"| {label} | {m.engineers_used} | {m.total_km} | {m.assigned} | {m.unassigned} | {m.violations} |"
        )
    lines += ["", "## Самопроверка", "", ("OK: " if check[0] else "FAIL: ") + check[1]]
    control = next((plan for label, plan in plans if plan.solver == "dispatchers"), None)
    if control is not None and control.violations:
        lines += ["", "## Нарушения в плане диспетчеров (первые 15)", ""]
        lines += [f"- {violation}" for violation in control.violations[:15]]
    if synthetic.skipped:
        lines += ["", "## Отброшенные строки", ""] + [f"- {item}" for item in synthetic.skipped]
    missing = [r for r in bundle.requests if r.geocode_precision == "none"]
    if missing:
        lines += ["", "## Адреса не найдены", ""] + [f"- {r.id}: {r.address}" for r in missing]
    return "\n".join(lines) + "\n"


def prepare_region(
    region: str,
    cfg: SynthConfig,
    *,
    repo_root: Path,
    geocoder: Geocoder | None,
    osrm: OsrmClient | None,
    cache: KVCache | None,
    time_limit_s: int,
    traffic: TrafficProfile,
) -> PrepareResult:
    region_cfg = cfg.regions[region]
    control = parse_beeline_csv((repo_root / region_cfg.control).read_bytes())
    synthetic = parse_beeline_csv((repo_root / region_cfg.synthetic).read_bytes())
    check_alignment(synthetic, control)
    if not synthetic.office_address:
        raise ValueError(f"В файле {region_cfg.synthetic} нет строки «Адрес офиса»")

    geo_cache = JsonGeocodeCache(repo_root / "data" / "geocode_cache.json")
    try:
        office_geo = geocode_address(synthetic.office_address, "", geocoder, geo_cache)
        if office_geo.lat is None or office_geo.lon is None:
            raise ValueError(f"Не удалось геокодировать адрес офиса: {synthetic.office_address}")
        office = Office(
            region=region,
            title=region_cfg.title,
            address=synthetic.office_address,
            lat=office_geo.lat,
            lon=office_geo.lon,
        )
        requests = build_requests(
            cfg,
            synthetic,
            control,
            lambda address, district: geocode_address(address, district, geocoder, geo_cache),
        )
    finally:
        geo_cache.save()

    row_points = {
        k: (r.lat, r.lon) for k, r in enumerate(requests) if r.lat is not None and r.lon is not None
    }
    engineers, crew_to_engineer = build_engineers(cfg, region, control, office, row_points)
    problem = make_problem(requests, engineers, model=TravelModel(), traffic=traffic, osrm=osrm, cache=cache)
    fcfs = FcfsSolver().solve(problem)
    optimized = OrToolsSolver(time_limit_s=time_limit_s).solve(problem)
    control_plan = build_control_plan(problem, control, synthetic, crew_to_engineer)
    events = build_demo_events(cfg, region, requests, control, synthetic, crew_to_engineer, optimized)
    bundle = Bundle(
        region=region,
        office=office,
        requests=requests,
        engineers=engineers,
        events=events,
        control_plan=control_plan,
    )
    check = self_check(fcfs.metrics, optimized.metrics)
    report = render_report(
        region_cfg.title,
        region,
        synthetic,
        bundle,
        problem.travel.base.source,
        [
            ("Базовый (FCFS по ТЗ)", fcfs),
            ("Оптимизированный (OR-Tools)", optimized),
            ("Диспетчеры (контрольное распределение)", control_plan),
        ],
        check,
    )
    return PrepareResult(bundle=bundle, fcfs=fcfs, optimized=optimized, report=report, self_check_ok=check[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Готовит бандлы данных по регионам из выгрузки Билайна")
    parser.add_argument("--region", default="all", help="east | south_east | south_center | all")
    parser.add_argument(
        "--osrm-url", default=os.environ.get("OSRM_URL"), help="например http://localhost:5000"
    )
    parser.add_argument("--geocoder", choices=["nominatim", "cache-only"], default="nominatim")
    parser.add_argument("--time-limit", type=int, default=3, help="секунд на OR-Tools")
    args = parser.parse_args(argv)

    cfg = SynthConfig.load(BACKEND_DIR / "config" / "synth_config.yaml")
    regions = list(cfg.regions) if args.region == "all" else [args.region]
    unknown = [region for region in regions if region not in cfg.regions]
    if unknown:
        parser.error(f"неизвестный регион: {', '.join(unknown)}")

    geocoder = NominatimGeocoder() if args.geocoder == "nominatim" else None
    osrm = OsrmClient(args.osrm_url) if args.osrm_url else None
    cache = KVCache(REPO_ROOT / "data" / "cache.sqlite")
    traffic = TrafficProfile.load(BACKEND_DIR / "config" / "traffic_profile.yaml")

    all_ok = True
    for region in regions:
        result = prepare_region(
            region,
            cfg,
            repo_root=REPO_ROOT,
            geocoder=geocoder,
            osrm=osrm,
            cache=cache,
            time_limit_s=args.time_limit,
            traffic=traffic,
        )
        out_dir = REPO_ROOT / "data" / "bundles" / region
        save_bundle(result.bundle, out_dir / "bundle.json")
        (out_dir / "report.md").write_text(result.report, encoding="utf-8")
        print(result.report)
        all_ok = all_ok and result.self_check_ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_prepare.py`
Expected: `2 passed`

- [ ] **Step 6: Прогнать весь набор и линтер**

Run: `cd backend && uv run pytest && uv run ruff check app tests`
Expected: `84 passed`, затем `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add backend/app/synth/control.py backend/app/synth/prepare.py backend/tests/test_prepare.py
git commit -m "feat(synth): dispatchers plan and prepare CLI with report and self-check" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 13: Бандлы трёх регионов на реальных данных

Первый запуск ходит в публичный Nominatim не чаще раза в секунду и занимает несколько минут. Повторные запуски берут координаты из `data/geocode_cache.json`. OSRM появится в Плане 2; здесь матрица строится по гаверсинусу, и отчёт это показывает. После подъёма OSRM бандлы пересобираются той же командой с `--osrm-url`.

**Files:**
- Create (генерируются): `data/bundles/east/bundle.json`, `data/bundles/east/report.md`, `data/bundles/south_east/bundle.json`, `data/bundles/south_east/report.md`, `data/bundles/south_center/bundle.json`, `data/bundles/south_center/report.md`, `data/geocode_cache.json`

**Interfaces:**
- Consumes: CLI из Task 12.
- Produces: бандлы, которые План 2 загружает как встроенные демо-наборы и использует для определения региона при загрузке сырого CSV.

- [ ] **Step 1: Собрать бандлы**

Run: `cd backend && uv run python -m app.synth.prepare --region all --time-limit 5`
Expected: код выхода 0 и три отчёта в консоли. Ориентир из проверочного прогона при планировании:

| Регион | План | Инженеров | Км | Назначено | Не назначено | Нарушений |
|---|---|---|---|---|---|---|
| Восток | Базовый (FCFS по ТЗ) | 12 | 421.95 | 56 | 10 | 0 |
| Восток | Оптимизированный (OR-Tools) | 7 | 150.12 | 66 | 0 | 0 |
| Восток | Диспетчеры (контрольное распределение) | 12 | 220.65 | 64 | 2 | 3 |
| Юго-восток | Базовый (FCFS по ТЗ) | 12 | 563.83 | 59 | 24 | 0 |
| Юго-восток | Оптимизированный (OR-Tools) | 9 | 164.1 | 83 | 0 | 0 |
| Юго-восток | Диспетчеры (контрольное распределение) | 12 | 251.62 | 83 | 0 | 20 |
| Югоцентр | Базовый (FCFS по ТЗ) | 10 | 305.83 | 50 | 6 | 0 |
| Югоцентр | Оптимизированный (OR-Tools) | 5 | 112.81 | 56 | 0 | 0 |
| Югоцентр | Диспетчеры (контрольное распределение) | 11 | 147.94 | 56 | 0 | 4 |

Геокодирование при планировании: Восток 62 дома / 4 улицы, Юго-восток 78 / 5, Югоцентр 55 / 1, не найдено ни одного адреса. OR-Tools с лимитом времени может давать немного другие километры, число инженеров и неназначенных должно совпасть или быть лучше.

- [ ] **Step 2: Проверить отчёты глазами**

Run: `cat ../data/bundles/*/report.md`
Expected: в каждом регионе строка `OK: Базовый вариант уступает оптимизированному…`; оптимизированный план не хуже FCFS по числу неназначенных. Если самопроверка упала, остановиться и сообщить человеку: веса и конфиг синтеза без согласования не менять.

- [ ] **Step 3: Проверить воспроизводимость**

Заявки, инженеры и план диспетчеров детерминированы. Демо-события зависят от решения OR-Tools с лимитом времени и могут отличаться между запусками, поэтому сравниваются бандлы без поля `events`.

Run:
```bash
cd backend
cat > /tmp/bundle_digest.py <<'PY'
import hashlib, json, pathlib
for path in sorted(pathlib.Path("../data/bundles").glob("*/bundle.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("events")
    print(path.parent.name, hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16])
PY
uv run python /tmp/bundle_digest.py > /tmp/bundles_before.txt
uv run python -m app.synth.prepare --region all --geocoder cache-only --time-limit 5 > /dev/null
uv run python /tmp/bundle_digest.py | diff /tmp/bundles_before.txt - && echo "bundles reproducible"
```
Expected: `bundles reproducible`.

- [ ] **Step 4: Commit**

```bash
git add data/bundles data/geocode_cache.json
git commit -m "data: build region bundles and geocode cache from Beeline exports" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```
