from __future__ import annotations

from enum import StrEnum


class Skill(StrEnum):
    LOCAL = "local"
    CONNECTION = "connection"
    EMERGENCY = "emergency"


# Прежнее значение типа «Пешеход». С 16.09.2026 пешком и на общественном транспорте — один тип PUBLIC, а «foot»
# ещё встречается в старых бандлах, CSV и ответах модели: такое значение читается как общественный транспорт.
LEGACY_FOOT = "foot"


class Transport(StrEnum):
    CAR = "car"
    BIKE = "bike"
    PUBLIC = "public"  # общественный транспорт и пешком

    @classmethod
    def _missing_(cls, value: object) -> Transport | None:
        """Transport("foot") и pydantic-поля с «foot» дают PUBLIC. Наружу «foot» сервис не отдаёт."""
        return cls.PUBLIC if value == LEGACY_FOOT else None


class Priority(StrEnum):
    NORMAL = "normal"
    URGENT = "urgent"


class RequestTier(StrEnum):
    """Приоритет распределения из ответов организаторов (вопрос 15): авария → подключение → ремонт и дозаказ.

    Уровень определяется типом работ (тип заявки BK) и за день не меняется, поэтому он не то же самое, что
    Priority: «Срочная» отмечает состояние конкретной заявки (авария, срочная заявка диспетчера), а
    уровень — род работ. Ловушка данных: «Дозаказ» и «Подключение» делят навык connection, но лежат на разных
    уровнях, поэтому уровень берётся из типа BK, а не из навыка.
    """

    EMERGENCY = "emergency"
    CONNECTION = "connection"
    ROUTINE = "routine"


class RequestStatus(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    # «Сегодня не приедем»: диспетчер сказал клиенту по телефону (событие «Коммуникация»). Решатель заявку не получает,
    # но в плане она остаётся без инженера и считается в «Не назначено»: это наш провал, и метрика его показывает.
    POSTPONED = "postponed"


class EventType(StrEnum):
    URGENT = "urgent"
    CANCEL = "cancel"
    # Возврат отменённой заявки интерфейс и помощник больше не предлагают: передумать можно только в уведомлении
    # сразу после отмены, пока событие не ушло на сервер. Тип остаётся ради дней, уже сохранённых в Postgres
    # (их шкала переигрывается с возвратами), и ради API событий.
    RESTORE = "restore"
    ENGINEER_UNAVAILABLE = "engineer_unavailable"
    ENGINEER_TRANSPORT_CHANGED = "engineer_transport_changed"
    REQUEST_UPDATED = "request_updated"
    ENGINEER_DELAYED = "engineer_delayed"
    REQUEST_REASSIGNED = "request_reassigned"
    # «Коммуникация»: диспетчер позвонил клиенту и договорился — назвал окно или сказал, что сегодня не приедем.
    # Ставит его отметка ✓ на вкладке «Коммуникации»; помощник такое событие не предлагает.
    CLIENT_AGREED = "client_agreed"


class ReasonCode(StrEnum):
    NO_SKILL = "no_skill"
    NO_TRANSPORT = "no_transport"
    DOES_NOT_FIT = "does_not_fit_window_or_shift"
    NO_FREE_ENGINEER = "no_free_engineer_in_window"
    ADDRESS_NOT_FOUND = "address_not_found"
    POSTPONED = "postponed"


SKILL_RU = {
    Skill.LOCAL: "Локальные работы",
    Skill.CONNECTION: "Работы на подключение и дозаказы",
    Skill.EMERGENCY: "Аварийные работы",
}
TRANSPORT_RU = {
    Transport.CAR: "Автомобиль",
    Transport.BIKE: "Велосипед",
    Transport.PUBLIC: "Общественный транспорт и пешком",
}
PRIORITY_RU = {Priority.NORMAL: "Обычная", Priority.URGENT: "Срочная"}
TIER_RU = {
    RequestTier.EMERGENCY: "Авария",
    RequestTier.CONNECTION: "Подключение",
    RequestTier.ROUTINE: "Ремонт и дозаказ",
}

# Подпись срочной заявки для диспетчера: «URG-<номер>». Так её подписывает и фронт (frontend/src/lib/format.ts).
URGENT_PREFIX = "URG-"


def request_label(request_id: str, priority: Priority) -> str:
    """Номер заявки для диспетчера: у срочной впереди «URG-», у обычной номер как есть.

    Приставка только для показа: в событиях, API и бандле номер заявки остаётся сырым.
    """
    if priority == Priority.URGENT and not request_id.startswith(URGENT_PREFIX):
        return f"{URGENT_PREFIX}{request_id}"
    return request_id
