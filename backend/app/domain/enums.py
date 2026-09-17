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


class RequestStatus(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class EventType(StrEnum):
    URGENT = "urgent"
    CANCEL = "cancel"
    RESTORE = "restore"
    ENGINEER_UNAVAILABLE = "engineer_unavailable"
    ENGINEER_TRANSPORT_CHANGED = "engineer_transport_changed"
    REQUEST_UPDATED = "request_updated"
    ENGINEER_DELAYED = "engineer_delayed"
    REQUEST_REASSIGNED = "request_reassigned"


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
    Transport.BIKE: "Велосипед",
    Transport.PUBLIC: "Общественный транспорт и пешком",
}
PRIORITY_RU = {Priority.NORMAL: "Обычная", Priority.URGENT: "Срочная"}
