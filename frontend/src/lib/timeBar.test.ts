import { describe, expect, it } from 'vitest';
import type { EventType } from '../api/types';
import { makeDelayEvent, makePlanningState, makeTimeline, makeTimelineItem } from '../test/fixtures';
import { EVENT_LABELS } from './format';
import { byId } from './planView';
import {
  DAY_LAST_MINUTE,
  EVENT_SHORT_LABELS,
  newlyRejected,
  pausesAt,
  pinLeft,
  playEnd,
  rejectedNotice,
  sliderRange,
  sliderValue,
  timelineItemTitle,
  timelineMove,
  timelinePins,
  timelineStatusText,
} from './timeBar';

const state = makePlanningState();
const engineers = byId(state.engineers);
const requests = byId(state.requests);

describe('time bar', () => {
  it('names every event type with a short pin label', () => {
    expect(Object.keys(EVENT_SHORT_LABELS).sort()).toEqual(Object.keys(EVENT_LABELS).sort());
    const labels: Record<EventType, string> = {
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
    expect(EVENT_SHORT_LABELS).toEqual(labels);
  });

  it('describes the status of an event and its pin title', () => {
    const [applied, , , pending, rejected] = makeTimeline();
    expect(timelineStatusText(applied)).toBe('применено');
    expect(timelineStatusText(pending)).toBe('впереди');
    expect(timelineStatusText(rejected)).toBe('отклонено: Заявка 74198 уже выполнена.');
    expect(timelineItemTitle(applied, engineers, requests)).toBe('Отмена заявки 10135 в 09:30 · применено');
    expect(timelineItemTitle(pending, engineers, requests)).toBe('Задержка: Бригада Арташкин на 150 мин с 15:00 · впереди');
    expect(rejectedNotice(rejected, engineers, requests)).toBe('Событие Отмена заявки 74198 в 16:30 отклонено: Заявка 74198 уже выполнена.');
  });

  it('caps the slider at 23:59 and keeps the clock inside it', () => {
    expect(DAY_LAST_MINUTE).toBe(1439);
    expect(sliderRange({ from: 540, to: 1380 })).toEqual({ min: 540, max: 1380 });
    expect(sliderRange({ from: 360, to: 1440 })).toEqual({ min: 360, max: 1439 });
    const range = { min: 540, max: 1380 };
    expect(sliderValue('00:00', range)).toBe(540);
    expect(sliderValue('13:05', range)).toBe(785);
    expect(sliderValue('23:30', range)).toBe(1380);
    expect(playEnd({ from: 540, to: 1380 })).toBe(1380);
    expect(playEnd({ from: 540, to: 1440 })).toBe(1439);
    expect(pinLeft(range, 540)).toBe(0);
    expect(pinLeft(range, 960)).toBe(50);
  });

  it('groups pins of the same minute into one pin coloured by the worst status', () => {
    const range = { min: 540, max: 1380 };
    const timeline = makeTimeline();
    const pins = timelinePins(timeline, range);
    expect(pins.map((pin) => [pin.time, pin.items.map((item) => item.id), pin.status, pin.label])).toEqual([
      ['09:30', ['tl_1'], 'applied', 'Отмена'],
      ['13:00', ['tl_2', 'tl_3'], 'applied', 'Недоступен'],
      ['15:00', ['tl_4'], 'pending', 'Задержка'],
      ['16:30', ['tl_5'], 'rejected', 'Отмена'],
    ]);
    expect(pins[1].left).toBeCloseTo(pinLeft(range, 780));

    const mixed = [
      makeTimelineItem({ id: 'tl_7', event: makeDelayEvent({ time: '15:00' }), status: 'pending' }),
      makeTimelineItem({ id: 'tl_8', event: makeDelayEvent({ time: '15:00' }), status: 'rejected', reason: 'нет' }),
      makeTimelineItem({ id: 'tl_9', event: makeDelayEvent({ time: '15:00' }) }),
    ];
    expect(timelinePins(mixed, range).map((pin) => pin.status)).toEqual(['rejected']);
    expect(timelinePins(mixed.slice(0, 1).concat(mixed.slice(2)), range).map((pin) => pin.status)).toEqual(['pending']);
  });

  it('names an event that waits for a variant and shows it on its pin first', () => {
    const awaiting = makeTimelineItem({ id: 'tl_9', status: 'awaiting', variant: null });
    expect(timelineStatusText(awaiting)).toBe('ждёт выбора варианта');
    const pins = timelinePins([makeTimelineItem({ id: 'tl_8', event: { ...awaiting.event }, status: 'rejected', reason: 'нет' }), awaiting], { min: 540, max: 1380 });
    expect(pins[0].status).toBe('awaiting');
  });

  it('pauses the playback only at a minute with an event that was not rejected', () => {
    const timeline = makeTimeline();
    expect(pausesAt(timeline, 900)).toBe(true);
    expect(pausesAt(timeline, 780)).toBe(true);
    expect(pausesAt(timeline, 990)).toBe(false);
    expect(pausesAt(timeline, 901)).toBe(false);
  });

  it('finds events that became rejected since the previous state', () => {
    const before = makeTimeline();
    const after = before.map((item) => (item.id === 'tl_4' ? { ...item, status: 'rejected' as const, reason: 'Инженер уже недоступен.' } : item));
    expect(newlyRejected(before, after).map((item) => item.id)).toEqual(['tl_4']);
    const added = makeTimelineItem({ id: 'tl_6', status: 'rejected', reason: 'нет' });
    expect(newlyRejected(before, [...before, added]).map((item) => item.id)).toEqual(['tl_6']);
    expect(newlyRejected(before, before)).toEqual([]);
  });

  it('counts the events a clock move applied and tells a move back across events', () => {
    const timeline = makeTimeline();
    const at = (cursor: string, applied: string[]) => ({
      ...state,
      cursor,
      timeline: timeline.map((item) => ({ ...item, status: applied.includes(item.id) ? ('applied' as const) : item.status === 'rejected' ? item.status : ('pending' as const) })),
    });
    const morning = at('08:00', []);
    const afternoon = at('15:30', ['tl_1', 'tl_2', 'tl_3', 'tl_4']);
    expect(timelineMove(morning, afternoon)).toEqual({ applied: 4, back: false });
    expect(timelineMove(afternoon, at('13:30', ['tl_1', 'tl_2', 'tl_3']))).toEqual({ applied: 0, back: true });
    expect(timelineMove(afternoon, at('15:10', ['tl_1', 'tl_2', 'tl_3', 'tl_4']))).toEqual({ applied: 0, back: false });
    expect(timelineMove(afternoon, at('15:40', ['tl_1', 'tl_2', 'tl_3', 'tl_4']))).toEqual({ applied: 0, back: false });
  });
});
