import type { Engineer, Plan, PlanningState, ServiceRequest } from '../api/types';
import { toMinutes } from './format';

/** Ось по умолчанию 08:00–23:00, расширяется под данные, но не дальше 00:00–24:00. */
export const AXIS_DEFAULT_FROM = 8 * 60;
export const AXIS_DEFAULT_TO = 23 * 60;
export const AXIS_MAX = 24 * 60;

export interface TimeScale {
  from: number;
  to: number;
}

export interface TimelineBar {
  requestId: string;
  left: number;
  width: number;
  windowLeft: number;
  windowWidth: number;
  pinned: boolean;
  urgent: boolean;
  late: boolean;
  /** Визит выходит за видимую шкалу (например 49:13 в плане диспетчеров) */
  clipped: boolean;
  /** Сырые значения HH:MM для подсказки */
  label: string;
}

export interface TimelineRow {
  engineer: Engineer;
  shiftLeft: number;
  shiftWidth: number;
  unavailableLeft: number | null;
  bars: TimelineBar[];
}

export function timeScale(state: PlanningState, plan: Plan): TimeScale {
  const starts = state.engineers.map((engineer) => toMinutes(engineer.shift_start));
  const ends = state.engineers.map((engineer) => toMinutes(engineer.shift_end));
  for (const route of plan.routes) {
    for (const visit of route.visits) {
      starts.push(toMinutes(visit.start));
      ends.push(toMinutes(visit.end));
    }
  }
  const from = Math.max(0, Math.floor(Math.min(AXIS_DEFAULT_FROM, ...starts) / 60) * 60);
  const to = Math.min(AXIS_MAX, Math.ceil(Math.max(AXIS_DEFAULT_TO, ...ends) / 60) * 60);
  return { from, to: Math.max(to, from + 60) };
}

export function percent(scale: TimeScale, minutes: number): number {
  const value = ((minutes - scale.from) / (scale.to - scale.from)) * 100;
  return Math.min(100, Math.max(0, value));
}

export function hourTicks(scale: TimeScale): number[] {
  const ticks: number[] = [];
  for (let minute = scale.from; minute <= scale.to; minute += 60) ticks.push(minute);
  return ticks;
}

function buildRow(engineer: Engineer, plan: Plan, scale: TimeScale, requests: Map<string, ServiceRequest>): TimelineRow {
  const route = plan.routes.find((item) => item.engineer_id === engineer.id);
  const shiftStart = percent(scale, toMinutes(engineer.shift_start));
  const shiftEnd = percent(scale, toMinutes(engineer.shift_end));
  const bars = (route?.visits ?? []).map((visit) => {
    const request = requests.get(visit.request_id);
    const start = toMinutes(visit.start);
    const end = toMinutes(visit.end);
    const left = percent(scale, start);
    const right = percent(scale, end);
    const windowLeft = percent(scale, toMinutes(request?.window_start ?? visit.start));
    const windowRight = percent(scale, toMinutes(request?.window_end ?? visit.end));
    return {
      requestId: visit.request_id,
      left: Math.min(left, 99.5),
      width: Math.max(0.5, right - left),
      windowLeft,
      windowWidth: windowRight - windowLeft,
      pinned: visit.pinned,
      urgent: request?.priority === 'urgent',
      late: visit.late_min > 0,
      clipped: start < scale.from || end > scale.to,
      label: `${visit.start}–${visit.end}`,
    };
  });
  const unavailableFrom = engineer.available ? null : (engineer.unavailable_from ?? engineer.shift_start);
  return {
    engineer,
    shiftLeft: shiftStart,
    shiftWidth: shiftEnd - shiftStart,
    unavailableLeft: unavailableFrom === null ? null : percent(scale, toMinutes(unavailableFrom)),
    bars,
  };
}

const requestIndex = (state: PlanningState) => new Map(state.requests.map((request) => [request.id, request]));

export function timelineRows(state: PlanningState, plan: Plan, scale: TimeScale): TimelineRow[] {
  const requests = requestIndex(state);
  return state.engineers.map((engineer) => buildRow(engineer, plan, scale, requests));
}

/** Строка одного инженера для личного таймлайна на странице бригады: та же, что у него в общем таймлайне. */
export function timelineRow(state: PlanningState, plan: Plan, scale: TimeScale, engineer: Engineer): TimelineRow {
  return buildRow(engineer, plan, scale, requestIndex(state));
}
