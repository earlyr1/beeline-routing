import type { Engineer, HHMM, PlanEvent, ServiceRequest, Skill, Transport } from '../api/types';
import { isValidTime, toMinutes } from './format';

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

export function describeEvent(event: PlanEvent, engineers: Map<string, Engineer>): string {
  switch (event.type) {
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
