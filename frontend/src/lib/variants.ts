import type { AssignVariant, BaseVariant, Engineer, EventChoice, EventType, EventVariant, PlanEvent, PlanningState } from '../api/types';
import { brigadeName } from './format';

/** «Ломающие» события: для них сервер предлагает варианты исправления. */
export const CHOOSABLE_EVENTS: ReadonlySet<EventType> = new Set<EventType>([
  'urgent',
  'engineer_unavailable',
  'engineer_transport_changed',
  'engineer_delayed',
  'request_reassigned',
]);

/** Названия трёх готовых стратегий, как в ответах сервера. */
export const VARIANT_TITLES: Record<BaseVariant, string> = {
  optimal: 'Оптимально по дню',
  stable: 'Минимум перестановок',
  keep: 'Ничего не менять',
};

/** Приставка варианта «отдать заявку бригаде». */
const ASSIGN_PREFIX = 'assign:';

/** Токен варианта «отдать заявку этой бригаде». */
export function assignVariant(engineerId: string): AssignVariant {
  return `${ASSIGN_PREFIX}${engineerId}`;
}

/** Вариант «отдать заявку бригаде»: его считают по выбору диспетчера, а не заранее. */
export function isAssignVariant(variant: EventVariant): variant is AssignVariant {
  return variant.startsWith(ASSIGN_PREFIX);
}

/** Бригада из токена assign:<инженер>; null — это одна из трёх готовых стратегий. */
export function assignedEngineer(variant: EventVariant): string | null {
  return isAssignVariant(variant) ? variant.slice(ASSIGN_PREFIX.length) : null;
}

/**
 * Название стратегии для события, как в ответе сервера. У переназначения заявки «ничего не менять» —
 * вставка заявки в маршрут выбранной бригады: остальные маршруты остаются как есть.
 * У «отдать бригаде» имя бригады ищется среди бригад дня: там, где под рукой сам вариант, берите его title сервера.
 */
export function variantTitle(variant: EventVariant, eventType: EventType, engineers: Engineer[] = []): string {
  if (isAssignVariant(variant)) {
    const assigned = assignedEngineer(variant) ?? '';
    return `Отдать: ${brigadeName(engineers.find((item) => item.id === assigned)?.name ?? assigned)}`;
  }
  if (variant === 'keep' && eventType === 'request_reassigned') return 'Вставить в маршрут';
  return VARIANT_TITLES[variant];
}

/**
 * Событие окна выбора варианта с прежней бригадой переназначенной заявки: в окне сервер присылает событие без неё.
 * У применённого события прежнюю бригаду записал сервер на шкале дня, а в текущем плане заявка уже у новой.
 * У события, которое ждёт выбора или впереди, прежняя бригада — бригада заявки в текущем плане: он ещё до события.
 */
export function choiceEvent(choice: EventChoice, state: PlanningState): PlanEvent {
  const { event } = choice;
  if (event.type !== 'request_reassigned' || event.previous_engineer_id) return event;
  const item = (state.timeline ?? []).find((entry) => entry.id === choice.entry_id);
  if (item?.status === 'applied') return { ...event, previous_engineer_id: item.event.previous_engineer_id ?? null };
  const owner = state.plan.routes.find((route) => route.visits.some((visit) => visit.request_id === event.request_id))?.engineer_id;
  // Заявка без бригады или уже у выбранной: прежней бригады нет.
  return owner && owner !== event.engineer_id ? { ...event, previous_engineer_id: owner } : event;
}
