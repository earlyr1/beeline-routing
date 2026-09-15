import type {
  Engineer,
  HHMM,
  Plan,
  PlanDiff,
  PlanningState,
  Route,
  RouteLeg,
  ServiceRequest,
  Unassigned,
  Visit,
} from '../api/types';
import { formatDuration, formatKm, toMinutes } from './format';

export interface AssignmentInfo {
  engineerId: string;
  visit: Visit;
  order: number;
}

export type DiffMark = 'added' | 'moved' | 'removed' | 'shifted';

export const DIFF_MARK_LABELS: Record<DiffMark, string> = {
  added: 'Новое назначение',
  moved: 'Перенесена',
  removed: 'Снята',
  shifted: 'Сдвиг времени',
};

export function displayedPlan(state: PlanningState, showPrevious: boolean): Plan {
  return showPrevious && state.previous_plan ? state.previous_plan : state.plan;
}

export function byId<T extends { id: string }>(items: T[]): Map<string, T> {
  return new Map(items.map((item) => [item.id, item]));
}

export function engineerIdsOf(state: PlanningState): string[] {
  return state.engineers.map((engineer) => engineer.id);
}

export function assignmentIndex(plan: Plan): Map<string, AssignmentInfo> {
  const index = new Map<string, AssignmentInfo>();
  for (const route of plan.routes) {
    route.visits.forEach((visit, order) => index.set(visit.request_id, { engineerId: route.engineer_id, visit, order }));
  }
  return index;
}

export function unassignedIndex(plan: Plan): Map<string, Unassigned> {
  return new Map(plan.unassigned.map((item) => [item.request_id, item]));
}

export function routeRequestIds(plan: Plan, engineerId: string): string[] {
  return plan.routes.find((route) => route.engineer_id === engineerId)?.visits.map((visit) => visit.request_id) ?? [];
}

export function diffMarks(diff: PlanDiff | null): Map<string, DiffMark> {
  const marks = new Map<string, DiffMark>();
  if (!diff) return marks;
  const put = (requestId: string, mark: DiffMark) => {
    if (!marks.has(requestId)) marks.set(requestId, mark);
  };
  diff.added.forEach((item) => put(item.request_id, 'added'));
  diff.moved.forEach((item) => put(item.request_id, 'moved'));
  diff.removed.forEach((item) => put(item.request_id, 'removed'));
  diff.time_shifts.filter((item) => item.delta_min !== 0).forEach((item) => put(item.request_id, 'shifted'));
  return marks;
}

export interface DiffBadge {
  text: string;
  title?: string;
}

/** Подписи для плана до события: что произойдёт с заявкой после него. */
const UPCOMING_LABELS: Record<DiffMark, string> = {
  added: 'Будет назначена',
  moved: 'Будет перенесена',
  removed: 'Будет снята',
  shifted: 'Сдвинется время',
};

/** Бейдж изменения заявки; у переноса видно, от кого и к кому она ушла. */
export function diffBadge(
  mark: DiffMark,
  requestId: string,
  diff: PlanDiff | null,
  engineers: Map<string, Engineer>,
  previous: boolean,
): DiffBadge {
  const move = mark === 'moved' ? diff?.moved.find((item) => item.request_id === requestId) : undefined;
  if (!move) return { text: previous ? UPCOMING_LABELS[mark] : DIFF_MARK_LABELS[mark] };
  const name = (engineerId: string) => `«${engineers.get(engineerId)?.name ?? engineerId}»`;
  return {
    text: previous ? `Будет перенесена к ${name(move.to_engineer_id)}` : `Перенесена от ${name(move.from_engineer_id)}`,
    title: `Перенос от ${name(move.from_engineer_id)} к ${name(move.to_engineer_id)}`,
  };
}

export interface RouteStop {
  order: number;
  visit: Visit;
  request: ServiceRequest | undefined;
  /** Минут от начала работ до конца окна; меньше нуля означает опоздание. */
  slackMin: number | null;
}

export interface RouteSummary {
  engineer: Engineer;
  stops: RouteStop[];
  totalKm: number;
  travelMin: number;
  endOfWork: HHMM | null;
  sentences: string[];
}

function orderSentence(stops: RouteStop[], totalKm: number, planKm: number): string {
  let head = `Маршрут ${formatKm(totalKm)}`;
  if (planKm > 0) head += ` — ${Math.round((totalKm / planKm) * 100)}% пробега всего плана`;
  const starts = stops.flatMap((stop) => (stop.request ? [toMinutes(stop.request.window_start)] : []));
  if (starts.length < 2) return `${head}.`;
  const followsWindows = starts.every((start, index) => index === 0 || start >= starts[index - 1]);
  const order = followsWindows
    ? 'порядок визитов следует окнам заявок'
    : 'порядок отличается от порядка окон: визиты с пересекающимися окнами расставлены так, чтобы сократить переезды';
  return `${head}; ${order}.`;
}

function windowSentence(stops: RouteStop[]): string {
  const late = stops
    .map((stop) => ({ id: stop.visit.request_id, minutes: Math.max(stop.visit.late_min, -(stop.slackMin ?? 0)) }))
    .filter((item) => item.minutes > 0);
  if (late.length > 0) {
    return `С опозданием к окну: ${late.map((item) => `${item.id} на ${formatDuration(item.minutes)}`).join(', ')}.`;
  }
  const early = stops.filter((stop) => stop.request && toMinutes(stop.visit.start) < toMinutes(stop.request.window_start));
  if (early.length > 0) return `Раньше окна начинаются визиты: ${early.map((stop) => stop.visit.request_id).join(', ')}.`;
  let tightest: { id: string; slack: number } | null = null;
  for (const stop of stops) {
    if (stop.slackMin !== null && (tightest === null || stop.slackMin < tightest.slack)) {
      tightest = { id: stop.visit.request_id, slack: stop.slackMin };
    }
  }
  if (!tightest) return 'Все визиты начинаются внутри окон.';
  return `Все визиты начинаются внутри окон, минимальный запас до конца окна ${formatDuration(tightest.slack)} (заявка ${tightest.id}).`;
}

function shiftSentence(engineer: Engineer, endOfWork: HHMM): string {
  if (engineer.unavailable_from) return `Работы заканчиваются в ${endOfWork}, инженер недоступен с ${engineer.unavailable_from}.`;
  const left = toMinutes(engineer.shift_end) - toMinutes(endOfWork);
  if (left < 0) return `Работы заканчиваются в ${endOfWork}, позже конца смены в ${engineer.shift_end}.`;
  if (left === 0) return `Работы заканчиваются ровно к концу смены в ${engineer.shift_end}.`;
  return `Работы заканчиваются в ${endOfWork}, до конца смены в ${engineer.shift_end} остаётся ${formatDuration(left)}.`;
}

function pinnedSentence(stops: RouteStop[]): string | null {
  const pinned = stops.filter((stop) => stop.visit.pinned).map((stop) => stop.visit.request_id);
  if (pinned.length === 0) return null;
  if (pinned.length === 1) return `Визит ${pinned[0]} начат до события и закреплён: перепланирование его не меняет.`;
  return `Визиты ${pinned.join(', ')} начаты до события и закреплены: перепланирование их не меняет.`;
}

/** Маршрут инженера и объяснение шаблонными фразами, только по данным плана. */
export function routeSummary(state: PlanningState, plan: Plan, engineerId: string): RouteSummary | null {
  const engineer = state.engineers.find((item) => item.id === engineerId);
  if (!engineer) return null;
  const requests = byId(state.requests);
  const route = plan.routes.find((item) => item.engineer_id === engineerId);
  const stops: RouteStop[] = (route?.visits ?? []).map((visit, index) => {
    const request = requests.get(visit.request_id);
    return {
      order: index + 1,
      visit,
      request,
      slackMin: request ? toMinutes(request.window_end) - toMinutes(visit.start) : null,
    };
  });
  const totalKm = route?.total_km ?? 0;
  const travelMin = route?.total_travel_min ?? 0;
  let endOfWork: HHMM | null = null;
  for (const stop of stops) {
    if (endOfWork === null || toMinutes(stop.visit.end) > toMinutes(endOfWork)) endOfWork = stop.visit.end;
  }

  const sentences: string[] = [];
  if (stops.length === 0 || endOfWork === null) {
    sentences.push('В этом плане у инженера нет визитов.');
    if (engineer.unavailable_from) sentences.push(`Инженер недоступен с ${engineer.unavailable_from}.`);
  } else {
    sentences.push(orderSentence(stops, totalKm, plan.metrics.total_km), windowSentence(stops), shiftSentence(engineer, endOfWork));
    const pinned = pinnedSentence(stops);
    if (pinned) sentences.push(pinned);
  }
  return { engineer, stops, totalKm, travelMin, endOfWork, sentences };
}

/** Прямые отрезки от старта инженера через заявки: запасной вариант, пока нет геометрии OSRM. */
export function straightLegs(route: Route, engineer: Engineer, requests: Map<string, ServiceRequest>): RouteLeg[] {
  const legs: RouteLeg[] = [];
  let previous: [number, number] = [engineer.start_lon, engineer.start_lat];
  for (const visit of route.visits) {
    const request = requests.get(visit.request_id);
    if (!request || request.lat === null || request.lon === null) continue;
    const point: [number, number] = [request.lon, request.lat];
    legs.push({ to_request_id: visit.request_id, coordinates: [previous, point] });
    previous = point;
  }
  return legs;
}

/** Порядок списка: назначенные по времени начала, затем неназначенные по окну, отменённые в конце. */
export function sortRequestsForList(requests: ServiceRequest[], plan: Plan): ServiceRequest[] {
  const assigned = assignmentIndex(plan);
  const rank = (request: ServiceRequest) => (request.status === 'cancelled' ? 2 : assigned.has(request.id) ? 0 : 1);
  const time = (request: ServiceRequest) => toMinutes(assigned.get(request.id)?.visit.start ?? request.window_start);
  return [...requests].sort((a, b) => rank(a) - rank(b) || time(a) - time(b) || a.id.localeCompare(b.id));
}
