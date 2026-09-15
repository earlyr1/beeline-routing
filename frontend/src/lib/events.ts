import type { Engineer, HHMM, Plan, PlanEvent, ServiceRequest, Skill, Transport } from '../api/types';
import { addMinutes, isValidTime, laterTime, toMinutes, TRANSPORT_LABELS } from './format';

/** Длина окна срочной заявки по умолчанию, минут. */
export const URGENT_WINDOW_MIN = 120;

export interface PickedPoint {
  lat: number;
  lon: number;
}

export interface UrgentForm {
  address: string;
  point: PickedPoint | null;
  windowStart: HHMM;
  windowEnd: HHMM;
  durationMin: number;
  skill: Skill;
  transport: Transport | '';
  time: HHMM;
}

export function timeError(time: string, now: HHMM): string | null {
  if (!isValidTime(time)) return 'Укажите время в формате ЧЧ:ММ';
  if (toMinutes(time) < toMinutes(now)) return `Время события не может быть раньше ${now}`;
  return null;
}

export function validateUrgentForm(form: UrgentForm, now: HHMM): string[] {
  const errors: string[] = [];
  if (!form.address.trim() && !form.point) errors.push('Укажите адрес или точку на карте');
  if (!isValidTime(form.windowStart) || !isValidTime(form.windowEnd)) {
    errors.push('Укажите окно визита в формате ЧЧ:ММ');
  } else if (toMinutes(form.windowEnd) <= toMinutes(form.windowStart)) {
    errors.push('Конец окна должен быть позже начала');
  }
  if (!Number.isFinite(form.durationMin) || form.durationMin <= 0) errors.push('Длительность должна быть больше нуля');
  const time = timeError(form.time, now);
  if (time) errors.push(time);
  return errors;
}

/** Самое раннее начало смены среди доступных инженеров; null, если доступных нет. */
export function earliestShiftStart(engineers: Engineer[]): HHMM | null {
  let earliest: HHMM | null = null;
  for (const engineer of engineers) {
    if (!engineer.available) continue;
    if (earliest === null || toMinutes(engineer.shift_start) < toMinutes(earliest)) earliest = engineer.shift_start;
  }
  return earliest;
}

/** Окно срочной заявки по умолчанию: с времени события, но не раньше начала смен, длиной два часа. */
export function defaultUrgentWindow(time: HHMM, engineers: Engineer[]): Pick<UrgentForm, 'windowStart' | 'windowEnd'> {
  const dayStart = earliestShiftStart(engineers);
  const windowStart = dayStart === null ? time : laterTime(time, dayStart);
  return { windowStart, windowEnd: addMinutes(windowStart, URGENT_WINDOW_MIN) };
}

/** Сколько визитов инженера в плане начинаются в указанное время или позже. */
export function visitsFrom(plan: Plan, engineerId: string, time: HHMM): number {
  const from = toMinutes(time);
  const route = plan.routes.find((item) => item.engineer_id === engineerId);
  return route ? route.visits.filter((visit) => toMinutes(visit.start) >= from).length : 0;
}

/** Доступный инженер с наибольшим числом визитов после указанного времени; при равенстве первый по имени. */
export function busiestEngineerId(engineers: Engineer[], plan: Plan, time: HHMM): string | null {
  const ranked = engineers
    .filter((engineer) => engineer.available)
    .map((engineer) => ({ engineer, visits: visitsFrom(plan, engineer.id, time) }))
    .sort((a, b) => b.visits - a.visits || a.engineer.name.localeCompare(b.engineer.name, 'ru'));
  return ranked[0]?.engineer.id ?? null;
}

/** Транспорт, который предлагаем при смене: без машины обычно пересаживаются на велосипед, иначе выдают машину. */
export function defaultNewTransport(current: Transport): Transport {
  return current === 'car' ? 'bike' : 'car';
}

/** Сколько визитов инженера, начинающихся в указанное время или позже, требуют автомобиль. */
export function carRequiredVisitsFrom(plan: Plan, requests: ServiceRequest[], engineerId: string, time: HHMM): number {
  const carOnly = new Set(requests.filter((request) => request.transport_required === 'car').map((request) => request.id));
  const from = toMinutes(time);
  const route = plan.routes.find((item) => item.engineer_id === engineerId);
  return route ? route.visits.filter((visit) => toMinutes(visit.start) >= from && carOnly.has(visit.request_id)).length : 0;
}

/** Подсказка диспетчеру, когда инженер остаётся без машины, а в его маршруте есть заявки только для автомобиля. */
export function carDowngradeHint(count: number, time: HHMM): string {
  return `Заявок с требованием «${TRANSPORT_LABELS.car}» после ${time}: ${count}, их перераспределит оптимизатор`;
}

export function newUrgentId(timestamp: number): string {
  return `URG-${timestamp.toString(36).toUpperCase()}`;
}

export function buildUrgentEvent(form: UrgentForm, requestId: string): PlanEvent {
  const point = form.point;
  const address =
    form.address.trim() || (point ? `Точка на карте ${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}` : 'Срочная заявка');
  const request: ServiceRequest = {
    id: requestId,
    address,
    lat: point?.lat ?? null,
    lon: point?.lon ?? null,
    geocode_precision: point ? 'house' : 'none',
    district: '',
    duration_min: Math.round(form.durationMin),
    window_start: form.windowStart,
    window_end: form.windowEnd,
    priority: 'urgent',
    skill: form.skill,
    transport_required: form.transport === '' ? null : form.transport,
    status: 'active',
    source_type_bk: 'Срочная заявка диспетчера',
    source_type_hd: '',
  };
  return { type: 'urgent', time: form.time, request, request_id: null, engineer_id: null };
}

export const cancelEvent = (requestId: string, time: HHMM): PlanEvent => ({
  type: 'cancel',
  time,
  request: null,
  request_id: requestId,
  engineer_id: null,
});

export const restoreEvent = (requestId: string, time: HHMM): PlanEvent => ({
  type: 'restore',
  time,
  request: null,
  request_id: requestId,
  engineer_id: null,
});

export const unavailableEvent = (engineerId: string, time: HHMM): PlanEvent => ({
  type: 'engineer_unavailable',
  time,
  request: null,
  request_id: null,
  engineer_id: engineerId,
});

/** Смена транспорта: клиент передаёт только новый транспорт, прежний сервер берёт у инженера сам. */
export const transportChangeEvent = (engineerId: string, transport: Transport, time: HHMM): PlanEvent => ({
  type: 'engineer_transport_changed',
  time,
  request: null,
  request_id: null,
  engineer_id: engineerId,
  transport,
});

export function describeEvent(event: PlanEvent, engineers: Map<string, Engineer>): string {
  switch (event.type) {
    case 'engineer_transport_changed': {
      const name = engineers.get(event.engineer_id ?? '')?.name ?? event.engineer_id;
      const next = event.transport ? TRANSPORT_LABELS[event.transport] : 'другой транспорт';
      if (event.previous_transport) {
        return `Смена транспорта: ${name}, ${TRANSPORT_LABELS[event.previous_transport]} → ${next} с ${event.time}`;
      }
      return `Смена транспорта: ${name} на ${next} с ${event.time}`;
    }
    case 'urgent':
      return `Срочная заявка ${event.request?.id ?? ''} в ${event.time}`;
    case 'cancel':
      return `Отмена заявки ${event.request_id} в ${event.time}`;
    case 'restore':
      return `Возврат заявки ${event.request_id} в ${event.time}`;
    case 'engineer_unavailable':
      return `Инженер недоступен: ${engineers.get(event.engineer_id ?? '')?.name ?? event.engineer_id} с ${event.time}`;
  }
}
