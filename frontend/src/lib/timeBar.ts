import type { Engineer, EventType, HHMM, PlanningState, ServiceRequest, TimelineItem, TimelineStatus } from '../api/types';
import { describeEvent } from './events';
import { fromMinutes, isValidTime, toMinutes } from './format';
import { percent, type TimeScale } from './timeline';

/** Последняя минута дня: дальше часы не идут, сервер не принимает время позже 23:59. */
export const DAY_LAST_MINUTE = 23 * 60 + 59;

/** Короткие подписи событий на отметках шкалы дня. */
export const EVENT_SHORT_LABELS: Record<EventType, string> = {
  urgent: 'Срочная',
  engineer_unavailable: 'Недоступен',
  engineer_transport_changed: 'Транспорт',
  engineer_delayed: 'Задержка',
  cancel: 'Отмена',
  restore: 'Возврат',
  request_updated: 'Изменение',
  request_reassigned: 'Назначение',
  client_agreed: 'Звонок',
};

/** Статус события языком диспетчера: «применено», «впереди», «ждёт выбора варианта» или «отклонено: причина». */
export function timelineStatusText(item: TimelineItem): string {
  if (item.status === 'applied') return 'применено';
  if (item.status === 'pending') return 'впереди';
  if (item.status === 'awaiting') return 'ждёт выбора варианта';
  return item.reason ? `отклонено: ${item.reason}` : 'отклонено';
}

/** Подсказка отметки: описание события и его статус. */
export function timelineItemTitle(item: TimelineItem, engineers: Map<string, Engineer>, requests: Map<string, ServiceRequest>): string {
  return `${describeEvent(item.event, engineers, requests)} · ${timelineStatusText(item)}`;
}

/** Сообщение, когда сервер отклонил событие шкалы, пока считал планы впереди. */
export function rejectedNotice(item: TimelineItem, engineers: Map<string, Engineer>, requests: Map<string, ServiceRequest>): string {
  return `Событие ${describeEvent(item.event, engineers, requests)} отклонено: ${item.reason ?? 'причина не указана'}`;
}

/** Границы ползунка часов в минутах: от начала шкалы дня до её конца, но не позже 23:59. */
export interface SliderRange {
  min: number;
  max: number;
}

export function sliderRange(scale: TimeScale): SliderRange {
  return { min: scale.from, max: Math.min(scale.to, DAY_LAST_MINUTE) };
}

/** Положение ползунка: часы раньше начала шкалы стоят в её начале, подпись при этом показывает настоящее время. */
export function sliderValue(clock: HHMM, range: SliderRange): number {
  const minute = isValidTime(clock) ? toMinutes(clock) : range.min;
  return Math.min(range.max, Math.max(range.min, minute));
}

/** Где закончится проигрывание дня: конец шкалы, но не позже 23:59. */
export function playEnd(scale: TimeScale): number {
  return Math.min(scale.to, DAY_LAST_MINUTE);
}

/** Положение отметки над ползунком, % ширины. */
export function pinLeft(range: SliderRange, minute: number): number {
  return percent({ from: range.min, to: range.max }, minute);
}

/** Отметка шкалы: события одной минуты стоят в одной отметке, иначе верхняя закрывала бы остальные. */
export interface TimelinePin {
  minute: number;
  time: HHMM;
  left: number;
  items: TimelineItem[];
  /** Главный статус среди событий минуты: ждёт выбора, затем отклонено, затем впереди, затем применено. */
  status: TimelineStatus;
  /** Подпись первого события минуты. */
  label: string;
}

const STATUS_WEIGHT: Record<TimelineStatus, number> = { applied: 0, pending: 1, rejected: 2, awaiting: 3 };

export function timelinePins(timeline: TimelineItem[], range: SliderRange): TimelinePin[] {
  const byMinute = new Map<number, TimelineItem[]>();
  for (const item of timeline) {
    if (!isValidTime(item.event.time)) continue;
    const minute = toMinutes(item.event.time);
    byMinute.set(minute, [...(byMinute.get(minute) ?? []), item]);
  }
  return [...byMinute.entries()]
    .sort(([a], [b]) => a - b)
    .map(([minute, items]) => ({
      minute,
      time: fromMinutes(minute),
      left: pinLeft(range, minute),
      items,
      status: items.reduce<TimelineStatus>((worst, item) => (STATUS_WEIGHT[item.status] > STATUS_WEIGHT[worst] ? item.status : worst), 'applied'),
      label: EVENT_SHORT_LABELS[items[0].event.type],
    }));
}

/** Проигрывание останавливается на минуте, где есть событие, которое сервер не отклонил: план в эту минуту пересчитывается. */
export function pausesAt(timeline: TimelineItem[], minute: number): boolean {
  return timeline.some((item) => item.status !== 'rejected' && isValidTime(item.event.time) && toMinutes(item.event.time) === minute);
}

/** События, которые в новом состоянии отклонены, а в прежнем ещё не были. */
export function newlyRejected(before: TimelineItem[], after: TimelineItem[]): TimelineItem[] {
  const previous = new Map(before.map((item) => [item.id, item.status]));
  return after.filter((item) => item.status === 'rejected' && previous.get(item.id) !== 'rejected');
}

/** Что сделал переход плана: сколько событий шкалы применилось заново и ушли ли часы назад, сняв события. */
export interface TimelineMove {
  applied: number;
  back: boolean;
}

export const NO_TIMELINE_MOVE: TimelineMove = { applied: 0, back: false };

const appliedIds = (state: PlanningState) => (state.timeline ?? []).filter((item) => item.status === 'applied').map((item) => item.id);

export function timelineMove(before: PlanningState, after: PlanningState): TimelineMove {
  const was = new Set(appliedIds(before));
  const now = appliedIds(after);
  const kept = new Set(now);
  const applied = now.filter((id) => !was.has(id)).length;
  const unapplied = [...was].some((id) => !kept.has(id));
  const earlier = isValidTime(before.cursor ?? '') && isValidTime(after.cursor ?? '') && toMinutes(after.cursor) < toMinutes(before.cursor);
  return { applied, back: unapplied && earlier };
}
