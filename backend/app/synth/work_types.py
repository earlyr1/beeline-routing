"""Типы работ срочной заявки диспетчера и что каждый из них заполняет по нормативам организаторов.

Цифр здесь нет: навык, уровень распределения, длительность, требуемый транспорт и оборудование считают те же правила
config/synth_config.yaml, по которым собираются заявки бандлов (app/synth/requests.py). Типы с нормативами отдаёт
GET /api/config, и диалог срочной заявки заполняет форму ответом сервера, ничего не повторяя у себя.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.domain.enums import RequestTier, Skill, Transport
from app.synth.config import SynthConfig
from app.synth.requests import norm_duration, synth_needs_equipment, synth_transport_required


class WorkType(BaseModel):
    """Тип работ в диалоге срочной заявки и значения, которыми он заполняет форму."""

    title: str
    # Тип заявки BK и тип HD, по которым посчитаны нормативы; с ними же уходит заявка диспетчера.
    source_type_bk: str
    source_type_hd: str
    skill: Skill
    # Уровень распределения типа BK (авария → подключение → ремонт и дозаказ) — тот, что поставит заявке сервер.
    tier: RequestTier
    duration_min: int
    transport_required: Transport | None
    needs_equipment: bool
    # «Как можно скорее»: авария начинается в момент, когда она реально поступила (ответы организаторов).
    asap: bool


# Название для диспетчера, тип заявки BK и тип HD каждой строки таблицы нормативов организаторов
# («Подключение клиентов», «Аварии на ТКД», «Локальная заявка/ремонт у клиента», «Дозаказ оборудования»).
# Тип HD — тот, что называет саму работу: по нему правила конфига решают транспорт и оборудование. У ремонта
# такого типа HD нет, неисправность диспетчер не называет. Порядок — очередь распределения из ответа на вопрос 15
# (авария → подключение → ремонт и дозаказ); первый тип диалог выбирает сам.
URGENT_WORK_TYPES: tuple[tuple[str, str, str], ...] = (
    ("Авария", "Глобальная проблема", "Авария"),
    ("Подключение", "Подключение", "Заявка на подключение"),
    ("Ремонт у клиента", "Локальная заявка", ""),
    ("Дозаказ оборудования", "Дозаказ", "Дозаказ оборудования"),
)


def urgent_work_types(cfg: SynthConfig) -> list[WorkType]:
    """Типы работ с нормативами из конфига; тип, которого нет в skill_by_bk конфига, пропускается."""
    types: list[WorkType] = []
    for title, type_bk, type_hd in URGENT_WORK_TYPES:
        skill = cfg.skill_by_bk.get(type_bk)
        if skill is None:
            continue
        types.append(
            WorkType(
                title=title,
                source_type_bk=type_bk,
                source_type_hd=type_hd,
                skill=skill,
                # Как в planning/session.py: тип без уровня в конфиге срочная заявка получает как авария.
                tier=cfg.tier_by_bk.get(type_bk, RequestTier.EMERGENCY),
                duration_min=norm_duration(cfg, type_bk),
                transport_required=synth_transport_required(cfg, skill, type_hd),
                # Колонки «Подключение» у заявки диспетчера нет: оборудование решает тип HD.
                needs_equipment=synth_needs_equipment(cfg, type_hd, ""),
                # Срочные по самому типу работ (urgent_bk_types) — аварии: их не планируют в окно, а берут сразу.
                asap=type_bk in cfg.urgent_bk_types,
            )
        )
    return types
