import type { Engineer, Plan, PlanDiff, PlanningState, Route, RouteLeg, ServiceRequest, Unassigned, Visit } from '../api/types';
import { toMinutes } from './format';

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
