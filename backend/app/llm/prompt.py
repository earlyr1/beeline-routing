"""Сообщения для модели: правила и текущее состояние дня."""

from __future__ import annotations

import json
from typing import Any

from app.domain.enums import SKILL_RU, TRANSPORT_RU
from app.domain.timeutil import fmt_hhmm
from app.llm.client import Message
from app.planning.session import PlanningSession


def system_rules(now: str) -> str:
    return f"""Ты помощник диспетчера выездных инженеров. Ты не меняешь план сам: ты только предлагаешь изменения, а диспетчер подтверждает или отклоняет каждое.
Текущее время плана: {now}.
Правила:
1. Каждое изменение из сообщения оформляй отдельным вызовом инструмента. В одном сообщении может быть несколько изменений.
2. Используй только id инженеров и заявок из состояния дня. Инженера узнавай по фамилии в названии бригады, заявку по номеру, адресу или улице.
3. Время пиши в формате HH:MM и не раньше текущего времени плана. Если время не названо, используй текущее. «Утром» это 10:00, «в обед» 13:00, «после обеда» 14:00, «вечером» 18:00.
4. «Заболел», «не выйдет», «уехал», «машина сломалась, работать не сможет» означает недоступность инженера. «Клиент отказался», «отмена» означает отмену заявки. «Вернуть», «снова в силе» означает возврат отменённой заявки. Новая авария или срочный вызов означает срочную заявку.
5. Если инженер продолжает работать на другом транспорте («машина сломалась, пересел на велосипед», «дальше пешком», «поедет на метро», «выдали автомобиль»), это смена транспорта: вызови propose_engineer_transport_change с новым транспортом car, foot, bike или public. Текущий транспорт инженера есть в состоянии дня. Если сломалась машина, но не сказано, на чём инженер продолжит и продолжит ли, вызови ask_clarification.
6. Если просят поменять существующую заявку («перенести визит на вечер», «работы займут два часа», «клиент переехал», «нужен автомобиль», «сделать срочной»), это изменение заявки: вызови propose_request_update и передай только поля, которые меняются. Все изменения одной заявки передавай одним вызовом. Перенос визита это изменение заявки, а не отмена.
7. Для срочной заявки укажи адрес, окно, длительность и навык. Для аварии бери навык emergency и автомобиль, если не сказано иное. Если окно не названо, бери два часа от текущего времени.
8. Если непонятно, кого или что менять, вызови ask_clarification с коротким вопросом. Не выдумывай id.
9. В rationale одним-двумя предложениями по-русски объясни, какие слова сообщения привели к предложению."""


def session_context(session: PlanningSession) -> dict[str, Any]:
    assigned = {
        visit.request_id: (route.engineer_id, visit)
        for route in session.plan.routes
        for visit in route.visits
    }
    names = {engineer.id: engineer.name for engineer in session.engineers}
    requests = []
    for request in session.requests:
        engineer_id, visit = assigned.get(request.id, (None, None))
        requests.append(
            {
                "id": request.id,
                "address": request.address,
                "district": request.district,
                "window": f"{fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}",
                "duration_min": request.duration_min,
                "transport_required": request.transport_required.value
                if request.transport_required
                else None,
                "priority": request.priority.value,
                "skill": request.skill.value,
                "status": request.status.value,
                "engineer_id": engineer_id,
                "engineer_name": names.get(engineer_id) if engineer_id else None,
                "planned_start": fmt_hhmm(visit.start) if visit else None,
            }
        )
    return {
        "now": fmt_hhmm(session.now),
        "skills": {skill.value: label for skill, label in SKILL_RU.items()},
        "transport": {transport.value: label for transport, label in TRANSPORT_RU.items()},
        "engineers": [
            {
                "id": engineer.id,
                "name": engineer.name,
                "skills": [skill.value for skill in engineer.skills],
                "transport": engineer.transport.value,
                "shift": f"{fmt_hhmm(engineer.shift_start)}–{fmt_hhmm(engineer.shift_end)}",
                "available": engineer.available,
                "unavailable_from": (
                    fmt_hhmm(engineer.unavailable_from) if engineer.unavailable_from is not None else None
                ),
            }
            for engineer in session.engineers
        ],
        "requests": requests,
    }


def build_messages(text: str, session: PlanningSession) -> list[Message]:
    context = json.dumps(session_context(session), ensure_ascii=False)
    return [
        {"role": "system", "content": system_rules(fmt_hhmm(session.now))},
        {"role": "user", "content": f"Состояние дня (JSON):\n{context}\n\nСообщение диспетчера:\n{text}"},
    ]
