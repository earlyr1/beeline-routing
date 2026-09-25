import { describe, expect, it } from 'vitest';
import type { PlanEvent, PlanningState } from '../api/types';
import { makeEventChoice, makePlanningState, makeTimelineItem } from '../test/fixtures';
import type { AgreedMarks } from './communications';
import { clockAtDayEnd, dayOver, daySummary, SUMMARY_EVENT_LABELS } from './daySummary';

/** Шкала дня фикстуры кончается в 23:00: это максимум ползунка. */
const END = '23:00';
const at = (cursor: string, patch: Partial<PlanningState> = {}) => makePlanningState({ cursor, ...patch });
const row = (state: PlanningState, label: string, clock = END, agreed: AgreedMarks = {}) =>
  daySummary(state, agreed, clock).rows.find((item) => item.label === label);

const event = (type: PlanEvent['type'], patch: Partial<PlanEvent> = {}): PlanEvent => ({
  type,
  time: '13:00',
  request: null,
  request_id: null,
  engineer_id: null,
  ...patch,
});

describe('day summary', () => {
  it('knows when the clock reached the end of the day and the plan followed it', () => {
    expect(clockAtDayEnd(at('13:00'), '13:00')).toBe(false);
    expect(clockAtDayEnd(at('13:00'), END)).toBe(true);
    // Ползунок уже в конце, а план ещё на прежнем времени: итоги подождут ответа сервера.
    expect(dayOver(at('13:00'), END)).toBe(false);
    expect(dayOver(at(END), END)).toBe(true);
    // Время стоит на событии, которое ждёт выбора варианта: до конца дня оно не дошло.
    expect(dayOver(at(END, { pending_choice: makeEventChoice() }), END)).toBe(false);
    // Часы, которые на экране ушли назад, итоги закрывают, даже если план ещё в конце.
    expect(dayOver(at(END), '22:59')).toBe(false);
  });

  it('compares the morning plan with the plan at the end of the day', () => {
    const state = at(END);
    const { rows, end } = daySummary(state, {}, END);
    expect(end).toBe(END);
    expect(rows.map((item) => item.label)).toEqual([
      'Задействовано инженеров',
      'Суммарный пробег',
      'Назначено заявок',
      'Выполнено к 23:00',
      'Не назначено',
      'из них перенесено со звонком клиенту',
      'Нарушений ограничений',
      'Опоздания',
    ]);
    expect(row(state, 'Задействовано инженеров')).toMatchObject({ morning: '3', day: '2', delta: '−1', verdict: 'better' });
    expect(row(state, 'Суммарный пробег')).toMatchObject({ morning: '41,5 км', day: '34,9 км', delta: '−6,6 км', verdict: 'better' });
    // Набор заявок к концу дня другой (отмены, срочные): разница по назначенным не оценивается.
    expect(row(state, 'Назначено заявок')).toMatchObject({ morning: '7', day: '6', delta: '−1', verdict: 'same' });
    expect(row(state, 'Выполнено к 23:00')).toMatchObject({ morning: '—', day: '6', delta: '' });
    expect(row(state, 'Не назначено')).toMatchObject({ morning: '0', day: '1', delta: '+1', verdict: 'worse' });
    expect(row(state, 'из них перенесено со звонком клиенту')).toMatchObject({ morning: '0', day: '0', nested: true });
    expect(row(state, 'Нарушений ограничений')).toMatchObject({ morning: '1', day: '0', delta: '−1', verdict: 'better' });
    // Утром 10135 начиналась в 14:05 при окне до 12:00; в итоговом плане опозданий нет.
    expect(row(state, 'Опоздания')).toMatchObject({ morning: '1', day: '0', delta: '−1', verdict: 'better' });
  });

  it('counts done visits by the clock and late visits of the final plan', () => {
    const state = at(END);
    // К 13:00 закончились 74198, 86160 и 84627.
    expect(row(state, 'Выполнено к 13:00', '13:00')?.day).toBe('3');
    const routes = state.plan.routes.map((route) =>
      route.engineer_id === 'E02' ? { ...route, visits: route.visits.map((visit) => ({ ...visit, late_min: 25 })) } : route,
    );
    expect(row({ ...state, plan: { ...state.plan, routes } }, 'Опоздания')).toMatchObject({ day: '2', delta: '+1', verdict: 'worse' });
  });

  it('shows the postponed requests inside the unassigned ones without judging them', () => {
    const state = at(END);
    const requests = state.requests.map((request) => (request.id === '18754' ? { ...request, status: 'postponed' as const } : request));
    expect(row({ ...state, requests }, 'из них перенесено со звонком клиенту')).toMatchObject({
      morning: '0',
      day: '1',
      delta: '+1',
      verdict: 'same',
    });
  });

  it('leaves the morning blank without the morning metrics but still counts the morning late visits', () => {
    const state = at(END, { morning_metrics: null });
    expect(row(state, 'Суммарный пробег')).toMatchObject({ morning: '—', day: '34,9 км', delta: '', verdict: 'same' });
    expect(row(state, 'Опоздания')).toMatchObject({ morning: '1', day: '0' });
    expect(row(at(END, { morning_metrics: null, morning: [] }), 'Опоздания')).toMatchObject({ morning: '—', delta: '' });
  });

  it('compares the final plan with the baseline and the Beeline dispatchers', () => {
    expect(daySummary(at(END), {}, END).versus).toEqual([
      {
        against: 'к базовому (FCFS)',
        note: null,
        deltas: [
          { text: 'инженеров 0', verdict: 'same' },
          { text: 'пробег +2,7 км', verdict: 'worse' },
          { text: 'не назначено −1', verdict: 'better' },
        ],
      },
      {
        against: 'к диспетчерам Билайна',
        note: 'исходный день, события в нём не учтены',
        deltas: [
          { text: 'инженеров −1', verdict: 'better' },
          { text: 'пробег −5,1 км', verdict: 'better' },
          { text: 'не назначено +1', verdict: 'worse' },
        ],
      },
    ]);
  });

  it('says the dispatchers of a generated region are not people and skips a missing dispatcher plan', () => {
    const generated = daySummary(at(END, { generated: true, events: [] }), {}, END).versus[1];
    expect(generated).toMatchObject({
      against: 'к диспетчерам',
      note: 'не решения людей: регион сгенерирован нами, заявки разложила простая эвристика',
    });
    expect(daySummary(at(END, { control: null }), {}, END).versus.map((item) => item.against)).toEqual(['к базовому (FCFS)']);
  });

  it('counts only applied events by kind and whose variant the dispatcher chose', () => {
    const timeline = [
      makeTimelineItem({ id: 'tl_1', event: event('urgent'), variant: 'optimal' }),
      makeTimelineItem({ id: 'tl_2', event: event('cancel', { request_id: '10135' }), variant: 'keep', variant_auto: true }),
      makeTimelineItem({ id: 'tl_3', event: event('request_updated', { request_id: '50104' }), variant: 'keep', variant_auto: true }),
      makeTimelineItem({ id: 'tl_4', event: event('restore', { request_id: '10135' }), variant: 'keep', variant_auto: true }),
      makeTimelineItem({ id: 'tl_5', event: event('request_reassigned', { request_id: '50104' }), variant: 'assign:E02' }),
      makeTimelineItem({ id: 'tl_6', event: event('engineer_delayed', { engineer_id: 'E01' }), variant: 'keep', variant_auto: true }),
      makeTimelineItem({ id: 'tl_7', event: event('engineer_transport_changed', { engineer_id: 'E01' }), variant: 'stable' }),
      makeTimelineItem({ id: 'tl_8', event: event('client_agreed', { request_id: '18754' }), variant: 'keep', variant_auto: true }),
      // Впереди и отклонённые в итоги не входят.
      makeTimelineItem({ id: 'tl_9', event: event('cancel', { time: '23:30' }), status: 'pending' }),
      makeTimelineItem({ id: 'tl_10', event: event('urgent'), status: 'rejected', reason: 'Адрес не найден.' }),
    ];
    const { events } = daySummary(at(END, { timeline }), {}, END);
    expect(events.total).toBe(8);
    expect(events.kinds.map((item) => [item.label, item.count])).toEqual([
      [SUMMARY_EVENT_LABELS.urgent, 1],
      [SUMMARY_EVENT_LABELS.cancel, 1],
      [SUMMARY_EVENT_LABELS.edit, 2],
      [SUMMARY_EVENT_LABELS.reassign, 1],
      [SUMMARY_EVENT_LABELS.brigade, 2],
      [SUMMARY_EVENT_LABELS.call, 1],
    ]);
    expect(events).toMatchObject({ chosen: 3, auto: 5 });
    expect(daySummary(at(END), {}, END).events).toMatchObject({ total: 0, chosen: 0, auto: 0 });
  });

  it('counts the agreed clients and the ones still waiting for a call like the communications tab', () => {
    // 18754 так и осталась без бригады, а клиент ждёт её в 18:00–20:00: ему должны позвонить.
    expect(daySummary(at(END), {}, END).calls).toEqual({ agreed: 0, waiting: 1 });
    const agreed: AgreedMarks = {
      '18754': { window: null, entry_id: 'tl_8', applied: true },
      '46393': { window: { start: '14:00', end: '16:00' }, entry_id: 'tl_7', applied: true },
    };
    expect(daySummary(at(END), agreed, END).calls).toEqual({ agreed: 2, waiting: 0 });
  });

  it('does not count a broken agreement or a request cancelled after the call as agreed', () => {
    // Клиенту 18754 назвали 18:00–20:00, а заявка потом осталась без бригады: он снова ждёт звонка, и только там.
    const broken: AgreedMarks = { '18754': { window: { start: '18:00', end: '20:00' }, entry_id: 'tl_8', applied: true } };
    expect(daySummary(at(END), broken, END).calls).toEqual({ agreed: 0, waiting: 1 });
    // Клиенту 46393 назвали окно, а потом он отказался: договорённость потеряла смысл.
    const state = at(END);
    const requests = state.requests.map((request) => (request.id === '46393' ? { ...request, status: 'cancelled' as const } : request));
    const cancelled: AgreedMarks = { '46393': { window: { start: '14:00', end: '16:00' }, entry_id: 'tl_7', applied: true } };
    expect(daySummary({ ...state, requests }, cancelled, END).calls).toMatchObject({ agreed: 0 });
  });
});
