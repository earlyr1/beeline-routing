import type { PlanDiff, PlanningState, Proposal, ProposalStatus } from '../api/types';
import { formatKm, formatWindow, shortAddress, SKILL_LABELS, toMinutes, TRANSPORT_LABELS } from './format';
import { byId } from './planView';

export const PROPOSAL_STATUS_LABELS: Record<ProposalStatus, string> = {
  pending: 'Ждёт решения',
  approved: 'Применено',
  rejected: 'Отклонено',
  failed: 'Не применилось',
};

export function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

/** Подробности предложения языком диспетчера: что затронет изменение в текущем плане. */
export function proposalDetails(proposal: Proposal, state: PlanningState): string[] {
  const { event } = proposal;
  const requests = byId(state.requests);
  const engineers = byId(state.engineers);

  if (event.type === 'urgent' && event.request) {
    const request = event.request;
    const details = [
      `${shortAddress(request.address)} · окно ${formatWindow(request.window_start, request.window_end)}, ${request.duration_min} мин`,
      SKILL_LABELS[request.skill],
    ];
    if (request.transport_required) details.push(`Нужен транспорт: ${TRANSPORT_LABELS[request.transport_required]}`);
    return details;
  }

  if (event.type === 'engineer_unavailable') {
    const route = state.plan.routes.find((item) => item.engineer_id === event.engineer_id);
    const affected = route ? route.visits.filter((visit) => toMinutes(visit.start) >= toMinutes(event.time)).length : 0;
    const name = engineers.get(event.engineer_id ?? '')?.name ?? event.engineer_id;
    if (affected === 0) return [`У ${name} нет заявок после ${event.time}`];
    return [`В маршруте после ${event.time}: ${affected} ${plural(affected, 'заявка', 'заявки', 'заявок')}, их перераспределит оптимизатор`];
  }

  const request = requests.get(event.request_id ?? '');
  if (!request) return [];
  const details = [`${shortAddress(request.address)} · окно ${formatWindow(request.window_start, request.window_end)}`];
  for (const route of state.plan.routes) {
    const visit = route.visits.find((item) => item.request_id === request.id);
    if (visit) details.push(`Сейчас в маршруте: ${engineers.get(route.engineer_id)?.name ?? route.engineer_id}, начало ${visit.start}`);
  }
  return details;
}

export function diffSummary(diff: PlanDiff): string {
  return (
    `Новых назначений: ${diff.added.length} · перенесено: ${diff.moved.length} · снято: ${diff.removed.length} · ` +
    `инженеров ${diff.metrics_before.engineers_used} → ${diff.metrics_after.engineers_used} · ` +
    `пробег ${formatKm(diff.metrics_before.total_km)} → ${formatKm(diff.metrics_after.total_km)}`
  );
}

export function upsertProposal(list: Proposal[], proposal: Proposal): Proposal[] {
  const index = list.findIndex((item) => item.id === proposal.id);
  if (index < 0) return [...list, proposal];
  return list.map((item, position) => (position === index ? proposal : item));
}
