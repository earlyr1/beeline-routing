"""Инструменты, которыми модель предлагает изменения плана, и их описание для JSON-режима."""

from __future__ import annotations

import json
from typing import Any

from app.domain.models import MAX_DELAY_MIN, MIN_DELAY_MIN

_TIME = {
    "type": "string",
    "pattern": r"^\d{1,2}:\d{2}$",
    "description": "Время HH:MM. Если в сообщении времени нет, текущее время плана.",
}
_ASAP = {"type": "boolean", "description": "true, если просят приехать как можно скорее и окно не названо"}
_RATIONALE = {
    "type": "string",
    "description": "Одно-два предложения по-русски: какие слова сообщения привели к предложению.",
}

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "propose_urgent_request": {
        "description": (
            "Предложить добавить срочную заявку. Если окно не названо и нужно как можно скорее, "
            "передай asap=true без окна."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Адрес объекта, как в сообщении"},
                "asap": _ASAP,
                "window_start": {**_TIME, "description": "Начало окна визита HH:MM, если окно названо"},
                "window_end": {**_TIME, "description": "Конец окна визита HH:MM, если окно названо"},
                "duration_min": {"type": "integer", "minimum": 5, "maximum": 600},
                "skill": {"type": "string", "enum": ["local", "connection", "emergency"]},
                "transport_required": {
                    "type": "string",
                    "enum": ["car", "bike", "public", "none"],
                    "description": "none, если требований к транспорту нет",
                },
                "time": _TIME,
                "rationale": _RATIONALE,
            },
            "required": ["address", "duration_min", "skill", "rationale"],
        },
    },
    "propose_cancel": {
        "description": "Предложить отменить заявку.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "id заявки из состояния дня"},
                "time": _TIME,
                "rationale": _RATIONALE,
            },
            "required": ["request_id", "rationale"],
        },
    },
    "propose_engineer_unavailable": {
        "description": "Предложить отметить инженера недоступным с указанного времени до конца дня.",
        "parameters": {
            "type": "object",
            "properties": {
                "engineer_id": {"type": "string", "description": "id инженера из состояния дня"},
                "time": _TIME,
                "rationale": _RATIONALE,
            },
            "required": ["engineer_id", "rationale"],
        },
    },
    "propose_engineer_transport_change": {
        "description": (
            "Предложить сменить тип транспорта инженера с указанного времени: машина сломалась, "
            "пересел на велосипед, выдали автомобиль и т.п."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "engineer_id": {"type": "string", "description": "id или фамилия инженера из состояния дня"},
                "transport": {
                    "type": "string",
                    "enum": ["car", "bike", "public"],
                    "description": "Новый транспорт: car автомобиль, bike велосипед, "
                    "public общественный транспорт и пешком",
                },
                "time": _TIME,
                "rationale": _RATIONALE,
            },
            "required": ["engineer_id", "transport", "rationale"],
        },
    },
    "propose_request_update": {
        "description": (
            "Предложить изменить заявку: перенести окно, поменять длительность, адрес, навык, приоритет "
            "или требование к транспорту."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "description": "id заявки или часть её адреса из состояния дня",
                },
                "address": {"type": "string", "description": "Новый адрес, только если он меняется"},
                "window_start": {**_TIME, "description": "Новое начало окна визита HH:MM"},
                "window_end": {**_TIME, "description": "Новый конец окна визита HH:MM"},
                "duration_min": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 600,
                    "description": "Новая длительность работ в минутах",
                },
                "skill": {"type": "string", "enum": ["local", "connection", "emergency"]},
                "priority": {"type": "string", "enum": ["normal", "urgent"]},
                "transport_required": {
                    "type": "string",
                    "enum": ["car", "bike", "public", "none"],
                    "description": "Новое требование к транспорту; none снимает требование",
                },
                "asap": {**_ASAP, "description": f"{_ASAP['description']}; false снимает «как можно скорее»"},
                "time": _TIME,
                "rationale": _RATIONALE,
            },
            "required": ["request_id", "rationale"],
        },
    },
    "propose_engineer_delay": {
        "description": (
            "Предложить отметить задержку инженера: застрял в пробке, работа на объекте затянулась и т.п. "
            "delay_min — на сколько минут задерживается."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "engineer_id": {"type": "string", "description": "id или фамилия инженера из состояния дня"},
                "delay_min": {
                    "type": "integer",
                    "minimum": MIN_DELAY_MIN,
                    "maximum": MAX_DELAY_MIN,
                    "description": "На сколько минут задерживается инженер",
                },
                "time": _TIME,
                "rationale": _RATIONALE,
            },
            "required": ["engineer_id", "delay_min", "rationale"],
        },
    },
    "not_understood": {
        "description": (
            "Сказать диспетчеру, что сообщение не понято: непонятно, кого или что менять, или не хватает данных. "
            "Вопросов не задавай: памяти между сообщениями нет, диспетчер напишет сообщение целиком заново."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Коротко, без вопроса, с маленькой буквы: чего не хватает или что неоднозначно, "
                    "например «не сказано, на сколько задерживается инженер»",
                }
            },
            "required": ["reason"],
        },
    },
}

TOOL_NAMES = tuple(TOOL_SPECS)


def openai_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": name, "description": spec["description"], "parameters": spec["parameters"]},
        }
        for name, spec in TOOL_SPECS.items()
    ]


def json_mode_instruction() -> str:
    schemas = {
        name: {"description": spec["description"], **spec["parameters"]} for name, spec in TOOL_SPECS.items()
    }
    return (
        "Вызов инструментов недоступен. Ответь только JSON-объектом без текста вокруг: "
        '{"actions": [{"tool": "<имя действия>", "arguments": {...}}]}. '
        "Если изменений нет, верни пустой список actions. Действия и их аргументы (JSON Schema):\n"
        + json.dumps(schemas, ensure_ascii=False)
    )
