"""Русские тексты ошибок проверки данных pydantic: диспетчер и помощник не видят английских сообщений."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_FIXED = {
    "missing": "обязательное поле",
    "int_parsing": "нужно целое число",
    "int_type": "нужно целое число",
    "int_from_float": "нужно целое число без дробной части",
    "float_parsing": "нужно число",
    "float_type": "нужно число",
    "string_type": "нужна строка",
    "bool_parsing": "нужно «да» или «нет»",
    "bool_type": "нужно «да» или «нет»",
    "dict_type": "нужен объект",
    "model_type": "нужен объект",
    "model_attributes_type": "нужен объект",
    "list_type": "нужен список",
    "extra_forbidden": "лишнее поле",
    "json_invalid": "некорректный JSON",
    "json_type": "некорректный JSON",
}

_BOUNDS = {
    "greater_than": ("gt", "значение должно быть больше {}"),
    "greater_than_equal": ("ge", "значение должно быть не меньше {}"),
    "less_than": ("lt", "значение должно быть меньше {}"),
    "less_than_equal": ("le", "значение должно быть не больше {}"),
    "string_too_short": ("min_length", "нужно не меньше {} символов"),
    "string_too_long": ("max_length", "нужно не больше {} символов"),
    "too_short": ("min_length", "нужно не меньше {} элементов"),
    "too_long": ("max_length", "нужно не больше {} элементов"),
}


def validation_message(item: Mapping[str, Any]) -> str:
    """Текст одной ошибки pydantic без поля. Свои ошибки моделей уже русские и возвращаются как есть."""
    kind = str(item.get("type", ""))
    ctx = item.get("ctx") or {}
    if kind in _FIXED:
        return _FIXED[kind]
    if kind in _BOUNDS:
        key, template = _BOUNDS[kind]
        if key in ctx:
            return template.format(ctx[key])
    if kind in ("enum", "literal_error") and "expected" in ctx:
        return "допустимые значения: " + str(ctx["expected"]).replace(" or ", " или ")
    return str(item.get("msg", "")).removeprefix("Value error, ")


def validation_text(errors: Sequence[Mapping[str, Any]], limit: int = 3, skip: Sequence[str] = ()) -> str:
    """Первые ошибки в виде «поле: текст» через точку с запятой; части пути из skip (например, body) не выводятся."""
    parts = []
    for item in errors[:limit]:
        location = ".".join(str(part) for part in item.get("loc", ()) if part not in skip)
        message = validation_message(item)
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts)
