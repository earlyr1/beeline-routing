"""Сообщения для модели: правила и текущее состояние дня."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from app.domain.enums import SKILL_RU, TRANSPORT_RU
from app.domain.timeutil import fmt_hhmm
from app.domain.windows import TimeSlot, slots_text
from app.llm.client import Message
from app.planning.session import PlanningSession


def window_rule(grid: Sequence[TimeSlot]) -> str:
    """Правило про сетку окон визита; без сетки (её задаёт конфиг) окно остаётся таким, как его назвали."""
    if not grid:
        return "Окно визита указывай так, как оно названо в сообщении."
    return (
        f"Окно визита называют не произвольным интервалом, а слотом сетки: {slots_text(grid)}. "
        "Если в сообщении названо другое время, возьми слот, в который оно попадает; если оно не попадает "
        "ни в один слот, вызови not_understood и коротко назови причину: время вне сетки окон. "
        "Окно «как можно скорее» слотом не бывает: для него передавай asap=true без окна, а если заявка "
        "перестаёт быть «как можно скорее», передай asap=false и назови слот."
    )


def system_rules(now: str, grid: Sequence[TimeSlot] = ()) -> str:
    return f"""Ты помощник диспетчера выездных инженеров. Ты не меняешь план сам: ты только предлагаешь изменения, а диспетчер подтверждает или отклоняет каждое.
Текущее время плана: {now}.
Правила:
1. Каждое изменение из сообщения оформляй отдельным вызовом инструмента. В одном сообщении может быть несколько изменений.
2. Используй только id инженеров и заявок из состояния дня. Инженера узнавай по фамилии в названии бригады, заявку по номеру, адресу или улице.
3. Время пиши в формате HH:MM и не раньше текущего времени плана. Если время не названо, используй текущее. «Утром» это 10:00, «в обед» 13:00, «после обеда» 14:00, «вечером» 18:00.
4. «Заболел», «не выйдет», «уехал», «машина сломалась, работать не сможет» означает недоступность инженера. «Клиент отказался», «отмена» означает отмену заявки. Вернуть отменённую заявку в план нельзя, возврат отменённой заявки не поддерживается: на «вернуть», «снова в силе» про отменённую заявку не вызывай ни одного инструмента, даже not_understood, — сервис сам ответит диспетчеру. Новая авария или срочный вызов означает срочную заявку.
5. Если инженер продолжает работать на другом транспорте («машина сломалась, пересел на велосипед», «дальше пешком», «поедет на метро», «выдали автомобиль»), это смена транспорта: вызови propose_engineer_transport_change с новым транспортом car, bike или public. Пешком и на общественном транспорте это один тип public: «дальше пешком» и «поедет на метро» означают public. Текущий транспорт инженера есть в состоянии дня. Если сломалась машина, но не сказано, на чём инженер продолжит и продолжит ли, вызови not_understood и коротко назови, чего не хватает.
6. Если просят поменять существующую заявку («перенести визит на вечер», «работы займут два часа», «клиент переехал», «нужен автомобиль», «сделать срочной»), это изменение заявки: вызови propose_request_update и передай только поля, которые меняются. Все изменения одной заявки передавай одним вызовом. Перенос визита это изменение заявки, а не отмена. Если к существующей заявке просят приехать как можно скорее, передай asap=true без окна. Если заявка перестаёт быть срочной («уже не горит», «можно в обычном порядке»), передай asap=false и окно визита.
7. Если инженер задерживается, но продолжит работу («застрял в пробке на полчаса», «работа на объекте затянулась на 40 минут», «опоздает на час»), это задержка инженера, а не недоступность: вызови propose_engineer_delay с delay_min в минутах от 5 до 480 («полчаса» это 30, «час» 60). Если не сказано, на сколько инженер задерживается, вызови not_understood и коротко назови, чего не хватает.
8. Для срочной заявки укажи адрес, длительность, навык и окно, если оно названо. Авария или срочная заявка без названного окна означает «как можно скорее»: передай asap=true без окна. Окно такой заявки ставит сервис, поэтому отсутствие окна не повод для not_understood: на новую аварию или срочный вызов по адресу без названного времени сразу вызывай propose_urgent_request с asap=true. Для аварии бери навык emergency и автомобиль, если не сказано иное.
9. {window_rule(grid)}
10. Памяти между сообщениями нет: каждое сообщение диспетчера приходит к тебе одно, без прошлых, и ответ на твой вопрос ты прочёл бы без самого вопроса. Поэтому вопросов не задавай и текстом не отвечай. Если непонятно, кого или что менять, или в сообщении не хватает данных, вызови not_understood и коротко, без вопроса назови, чего не хватает, например «не сказано, какой инженер заболел»: диспетчер напишет сообщение целиком заново. Не выдумывай id.
11. В rationale одним-двумя предложениями по-русски объясни, какие слова сообщения привели к предложению."""


def session_context(session: PlanningSession, now: int | None = None) -> dict[str, Any]:
    """Состояние дня для модели; now — текущее время плана (по умолчанию время последнего события сессии)."""
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
                "window": (
                    f"как можно скорее с {fmt_hhmm(request.window_start)}"
                    if request.asap
                    else f"{fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}"
                ),
                "asap": request.asap,
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
        "now": fmt_hhmm(session.now if now is None else now),
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


def build_messages(
    text: str, session: PlanningSession, now: int | None = None, grid: Sequence[TimeSlot] = ()
) -> list[Message]:
    """now — текущее время плана на шкале; без него время последнего события сессии. grid — сетка окон визита."""
    now = session.now if now is None else now
    context = json.dumps(session_context(session, now), ensure_ascii=False)
    return [
        {"role": "system", "content": system_rules(fmt_hhmm(now), grid)},
        {"role": "user", "content": f"Состояние дня (JSON):\n{context}\n\nСообщение диспетчера:\n{text}"},
    ]
