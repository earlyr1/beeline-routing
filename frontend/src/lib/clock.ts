import type { Engineer, HHMM, Route, RouteLeg, ServiceRequest, Visit } from '../api/types';
import type { LngLat } from '../components/map/yandexLoader';
import { toMinutes } from './format';

/**
 * Где инженер в момент на часах дня: ещё не выехал, в пути, ждёт у клиента, работает, обедает или закончил день.
 * Время выезда к визиту — приезд минус время в дороге: так его считает и сервер, когда прогоняет маршрут.
 */
export type PhaseKind = 'before' | 'driving' | 'waiting' | 'onSite' | 'lunch' | 'done';

export interface EnginePhase {
  kind: PhaseKind;
  /** Заявка, к которой относится фаза; null у обеда. */
  requestId: string | null;
  /** Фраза для подсказки маркера: «в пути к 50104». */
  text: string;
  /** Доля проеханного пути от 0 до 1; null — инженер никуда не едет. */
  progress: number | null;
}

/** Фаза инженера по его маршруту и времени на часах; null — визитов нет, и показывать нечего. */
export function enginePhase(route: Route | undefined, clock: HHMM): EnginePhase | null {
  if (!route || route.visits.length === 0) return null;
  const now = toMinutes(clock);
  // Обед идёт между визитами, поэтому он главнее: в его часы инженер не в дороге и не у клиента.
  if (route.lunch && now >= toMinutes(route.lunch.start) && now < toMinutes(route.lunch.end)) {
    return { kind: 'lunch', requestId: null, text: 'обед', progress: null };
  }
  for (const [index, visit] of route.visits.entries()) {
    const id = visit.request_id;
    if (now >= toMinutes(visit.end)) continue;
    if (now >= toMinutes(visit.start)) return { kind: 'onSite', requestId: id, text: `работает у ${id}`, progress: null };
    const arrival = toMinutes(visit.arrival);
    if (now >= arrival) return { kind: 'waiting', requestId: id, text: `ждёт у клиента ${id}`, progress: null };
    const departure = arrival - visit.leg_min;
    if (now >= departure) {
      // Приезд не позже выезда: инженер уже на месте назначения.
      const progress = arrival > departure ? (now - departure) / (arrival - departure) : 1;
      return { kind: 'driving', requestId: id, text: `в пути к ${id}`, progress };
    }
    // Между работами бывает пауза: инженер стоит там, где закончил, и ждёт выезда.
    return { kind: 'before', requestId: id, text: index === 0 ? 'ещё не выехал' : `ждёт выезда к ${id}`, progress: null };
  }
  return { kind: 'done', requestId: route.visits[route.visits.length - 1].request_id, text: 'работы закончены', progress: null };
}

const DEGREE = Math.PI / 180;

/** Длина отрезка в градусах широты: градус долготы к северу короче, иначе доля пути уезжала бы вбок. */
function segmentLength([lon1, lat1]: LngLat, [lon2, lat2]: LngLat): number {
  return Math.hypot((lon2 - lon1) * Math.cos(((lat1 + lat2) / 2) * DEGREE), lat2 - lat1);
}

function segmentLengths(coordinates: LngLat[]): { steps: number[]; total: number } {
  const steps: number[] = [];
  let total = 0;
  for (let index = 1; index < coordinates.length; index += 1) {
    const step = segmentLength(coordinates[index - 1], coordinates[index]);
    steps.push(step);
    total += step;
  }
  return { steps, total };
}

/** Ломаная, разрезанная в точке доли пути: проеханная часть и оставшаяся. Части короче двух точек не рисуются. */
export function splitAlong(coordinates: LngLat[], fraction: number): { passed: LngLat[]; rest: LngLat[] } {
  if (coordinates.length < 2 || fraction <= 0) return { passed: [], rest: coordinates };
  if (fraction >= 1) return { passed: coordinates, rest: [] };
  const { steps, total } = segmentLengths(coordinates);
  if (total === 0) return { passed: [], rest: coordinates };
  let left = fraction * total;
  for (let index = 0; index < steps.length; index += 1) {
    if (left <= steps[index]) {
      const share = steps[index] === 0 ? 0 : left / steps[index];
      const [lon1, lat1] = coordinates[index];
      const [lon2, lat2] = coordinates[index + 1];
      const point: LngLat = [lon1 + (lon2 - lon1) * share, lat1 + (lat2 - lat1) * share];
      return { passed: [...coordinates.slice(0, index + 1), point], rest: [point, ...coordinates.slice(index + 1)] };
    }
    left -= steps[index];
  }
  return { passed: coordinates, rest: [] };
}

/** Точка на ломаной по доле пути: доля считается по длине отрезков, а не по их числу. */
export function pointAlong(coordinates: LngLat[], fraction: number): LngLat | null {
  if (coordinates.length === 0) return null;
  const { passed, rest } = splitAlong(coordinates, fraction);
  return passed[passed.length - 1] ?? rest[0] ?? null;
}

/** Линия маршрута, разрезанная точкой инженера. */
export interface LegSplit {
  requestId: string;
  passed: LngLat[];
  rest: LngLat[];
}

export interface EnginePlace {
  phase: EnginePhase;
  /** Где инженер сейчас, [lon, lat]; null — точку негде взять. */
  point: LngLat | null;
  /** Заявки, к которым инженер уже доехал: дорога к ним позади. */
  driven: string[];
  /** Отрезок, по которому инженер едет сейчас; null — он стоит или линии маршрута нет. */
  split: LegSplit | null;
}

export interface PlaceInput {
  engineer: Engineer;
  route: Route | undefined;
  requests: Map<string, ServiceRequest>;
  /** Линии маршрута этого инженера: дороги OSRM или прямые отрезки. */
  legs: RouteLeg[];
  clock: HHMM;
}

/** Фаза инженера и его место на карте: точка, пройденные отрезки и текущий отрезок, разрезанный этой точкой. */
export function enginePlace({ engineer, route, requests, legs, clock }: PlaceInput): EnginePlace | null {
  const phase = enginePhase(route, clock);
  if (!phase || !route) return null;
  const now = toMinutes(clock);
  const start: LngLat = [engineer.start_lon, engineer.start_lat];
  const pointOf = (requestId: string): LngLat | null => {
    const request = requests.get(requestId);
    return request && request.lat !== null && request.lon !== null ? [request.lon, request.lat] : null;
  };
  /** Последняя известная точка маршрута до визита с этим номером: там инженер ждёт или стоит без координат заявки. */
  const pointBefore = (index: number): LngLat => {
    for (let back = index - 1; back >= 0; back -= 1) {
      const point = pointOf(route.visits[back].request_id);
      if (point) return point;
    }
    return start;
  };
  const driven = route.visits.filter((visit) => toMinutes(visit.arrival) <= now).map((visit) => visit.request_id);
  const place = (point: LngLat | null, split: LegSplit | null = null): EnginePlace => ({ phase, point, driven, split });

  if (phase.kind === 'lunch') {
    // Обед проходит там, где инженер освободился: у последнего клиента до обеда или в точке старта.
    const lunchStart = toMinutes(route.lunch?.start ?? clock);
    return place(pointBefore(route.visits.filter((visit) => toMinutes(visit.end) <= lunchStart).length));
  }
  if (phase.kind === 'done') return place(pointBefore(route.visits.length));
  const index = route.visits.findIndex((visit) => visit.request_id === phase.requestId);
  if (index < 0) return place(start);
  if (phase.kind === 'before') return place(pointBefore(index));
  if (phase.kind !== 'driving') return place(pointOf(route.visits[index].request_id) ?? pointBefore(index));
  // Заявка без координат или без линии маршрута: инженера не по чему вести, он остаётся в предыдущей точке.
  const leg = legs.find((item) => item.to_request_id === phase.requestId);
  if (!leg || leg.coordinates.length < 2) return place(pointBefore(index));
  const { passed, rest } = splitAlong(leg.coordinates, phase.progress ?? 0);
  return place(passed[passed.length - 1] ?? rest[0] ?? pointBefore(index), { requestId: leg.to_request_id, passed, rest });
}

/** Что с заявкой к времени на часах: выполнена, в работе или инженер уже в пути к ней. */
export type RequestClockStatus = 'done' | 'working' | 'driving';

export const REQUEST_CLOCK_LABELS: Record<RequestClockStatus, string> = {
  done: 'Выполнена',
  working: 'В работе',
  driving: 'В пути',
};

/** Статус заявки по её визиту и часам; null — работы ещё впереди или инженер уже ждёт у клиента. */
export function requestClockStatus(visit: Visit | undefined, clock: HHMM): RequestClockStatus | null {
  if (!visit) return null;
  const now = toMinutes(clock);
  if (now >= toMinutes(visit.end)) return 'done';
  if (now >= toMinutes(visit.start)) return 'working';
  const arrival = toMinutes(visit.arrival);
  return now >= arrival - visit.leg_min && now < arrival ? 'driving' : null;
}
