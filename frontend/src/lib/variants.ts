import type { EventType, EventVariant } from '../api/types';

/** «Ломающие» события: для них сервер предлагает варианты исправления. */
export const CHOOSABLE_EVENTS: ReadonlySet<EventType> = new Set<EventType>([
  'urgent',
  'engineer_unavailable',
  'engineer_transport_changed',
  'engineer_delayed',
]);

/** Названия стратегий исправления, как в ответах сервера. */
export const VARIANT_TITLES: Record<EventVariant, string> = {
  optimal: 'Оптимально по дню',
  stable: 'Минимум перестановок',
  keep: 'Ничего не менять',
};
