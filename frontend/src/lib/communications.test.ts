import { describe, expect, it } from 'vitest';
import type { HHMM, PlanningState } from '../api/types';
import { makePlanningState, makeTimelineItem } from '../test/fixtures';
import {
  callAgreedText,
  callChangeText,
  callContext,
  callList,
  eventsAhead,
  plannedPromise,
  type AgreedTimes,
} from './communications';

/** План фикстуры на 13:00: 50104 уехала к Арташкину, 46393 сдвинулась на 15:10, 18754 осталась без бригады. */
const state = (overrides: Partial<PlanningState> = {}) => makePlanningState(overrides);

/** Список звонков по текущему плану; на часах фикстуры 13:00. */
const calls = (planning: PlanningState, agreed: AgreedTimes = {}, clock: HHMM = '13:00') =>
  callList(planning, agreed, callContext(planning, false, clock));

const ids = (rows: { requestId: string }[]) => rows.map((row) => row.requestId);
const row = (list: ReturnType<typeof calls>['pending'], requestId: string) =>
  list.find((item) => item.requestId === requestId)!;

/** Утренний план фикстуры с другим временем одной заявки. */
function morningAt(requestId: string, start: string): PlanningState {
  const base = makePlanningState();
  return { ...base, morning: base.morning.map((item) => (item.request_id === requestId ? { ...item, start } : item)) };
}

describe('callList', () => {
  it('compares the plan with the morning while the client has heard nothing and sorts the calls by urgency', () => {
    const { pending, agreed } = calls(state(), {});

    // Красная строка впереди, дальше по времени визита: срочная в 13:05, 50104 в 14:00, 46393 в 15:10.
    expect(ids(pending)).toEqual(['18754', 'URG-001', '50104', '46393']);
    expect(agreed).toEqual([]);
    // Клиенту сказали 18:00, а бригада Комарь стала недоступна: сегодня к нему не приедут.
    expect(row(pending, '18754')).toMatchObject({ kind: 'lost', severity: 'red', was: '18:00', now: null });
    // Заявка переехала к другой бригаде, время прежнее: звонок нужен, но это не срыв.
    expect(row(pending, '50104')).toMatchObject({
      kind: 'moved',
      severity: 'grey',
      was: '14:00',
      now: '14:00',
      previousEngineerId: 'E02',
      engineerId: 'E01',
    });
    expect(row(pending, '46393')).toMatchObject({ kind: 'moved', severity: 'grey', was: '15:00', now: '15:10' });
    // Срочной заявки утром не было: клиенту ничего не обещали, с ним договариваются о времени.
    expect(row(pending, 'URG-001')).toMatchObject({ kind: 'added', severity: 'grey', was: null, now: '13:05', label: 'URG-001' });
    // Отменённая 10135 в список не попадает: клиент уже знает.
    expect(ids(pending)).not.toContain('10135');
  });

  it('calls a shift of an hour or more yellow and a visit outside the window red', () => {
    const shifted = morningAt('46393', '13:00');
    expect(row(calls(shifted, {}).pending, '46393')).toMatchObject({ severity: 'yellow', was: '13:00', now: '15:10' });

    const base = makePlanningState();
    const late = {
      ...base,
      plan: {
        ...base.plan,
        routes: base.plan.routes.map((route) => ({
          ...route,
          visits: route.visits.map((visit) => (visit.request_id === '50104' ? { ...visit, late_min: 20 } : visit)),
        })),
      },
    };
    // Визит начнётся позже конца окна клиента: обещание не выполняется, звонить так же срочно, как при срыве.
    expect(row(calls(late, {}).pending, '50104').severity).toBe('red');
  });

  it('keeps an agreed row out of the calls and brings it back when the visit moves again', () => {
    const agreedTimes: AgreedTimes = { '46393': { start: '15:10', engineer_id: 'E01' } };
    const settled = calls(state(), agreedTimes);
    expect(ids(settled.pending)).toEqual(['18754', 'URG-001', '50104']);
    expect(ids(settled.agreed)).toEqual(['46393']);

    // Следующее событие увезло визит обратно на утреннее время: клиент ждёт в 15:10, строка возвращается.
    const back = makePlanningState();
    const moved = {
      ...back,
      plan: {
        ...back.plan,
        routes: back.plan.routes.map((route) => ({
          ...route,
          visits: route.visits.map((visit) => (visit.request_id === '46393' ? { ...visit, start: '15:00' } : visit)),
        })),
      },
    };
    const again = calls(moved, agreedTimes);
    expect(again.agreed).toEqual([]);
    expect(row(again.pending, '46393')).toMatchObject({ was: '15:10', now: '15:00' });
  });

  it('remembers an agreed «сегодня не приедем» and says nothing about a request nobody was promised', () => {
    const list = calls(state(), { '18754': { start: null, engineer_id: null } });
    expect(ids(list.pending)).not.toContain('18754');
    expect(list.agreed.map(callAgreedText)).toEqual(['сказали, что сегодня не приедем']);
    // Заявка без визита и утром, и сейчас: обещать было нечего, строки нет.
    const nothing = makePlanningState({ morning: [] });
    expect(ids(calls({ ...nothing, plan: { ...nothing.plan, routes: [] } }, {}).pending)).toEqual([]);
  });

  it('describes the change in the words of the dispatcher', () => {
    const { pending } = calls(state(), {});
    expect(callChangeText(row(pending, '18754'))).toBe('было 18:00 → сегодня не приедем');
    expect(callChangeText(row(pending, '46393'))).toBe('было 15:00 → стало 15:10');
    expect(callChangeText(row(pending, '50104'))).toBe('время прежнее, 14:00');
    expect(callChangeText(row(pending, 'URG-001'))).toBe('приедем в 13:05');
    expect(callAgreedText(row(pending, '46393'))).toBe('договорились на 15:10');
  });

  it('does not turn an agreed time around when the clock goes back behind the events', () => {
    // Диспетчер договорился на 15:10 в плане версии 5, потом отмотал часы: на экране план версии 4, где визит в 15:00.
    const back: AgreedTimes = { '46393': { start: '15:10', engineer_id: 'E01', version: 5 } };
    const rewound = calls(state(), back);
    expect(ids(rewound.pending)).not.toContain('46393');
    expect(ids(rewound.agreed)).not.toContain('46393');

    // Часы вернулись к тому же плану: отметка снова сравнивается, и звонить не о чем.
    const same: AgreedTimes = { '46393': { start: '15:10', engineer_id: 'E01', version: 4 } };
    expect(ids(calls(state(), same).agreed)).toEqual(['46393']);
  });

  it('lets go of the visits that are already over', () => {
    // К девяти вечера все визиты плана закончились: звонить остаётся только тому, к кому сегодня не приедут.
    expect(ids(calls(state(), {}, '21:00').pending)).toEqual(['18754']);
    // И до конца визита строка на месте: 46393 идёт до 15:55.
    expect(ids(calls(state(), {}, '15:30').pending)).toContain('46393');
  });

  it('calls a broken agreement yellow even when the visit moved by less than an hour', () => {
    // Клиенту назвали 15:00, а бригада приедет в 15:10: сдвиг мелкий, но обещание уже нарушено.
    const broken: AgreedTimes = { '46393': { start: '15:00', engineer_id: 'E01' } };
    expect(row(calls(state(), broken).pending, '46393')).toMatchObject({ severity: 'yellow', was: '15:00', now: '15:10' });
    // Без отметки тот же сдвиг остаётся серым: клиенту про него ещё не говорили.
    expect(row(calls(state(), {}).pending, '46393').severity).toBe('grey');
  });

  it('sees the events the clock has not reached yet', () => {
    expect(eventsAhead(state())).toBe(false);
    expect(eventsAhead(state({ timeline: [makeTimelineItem({ status: 'pending' })] }))).toBe(true);
    expect(eventsAhead(state({ timeline: [makeTimelineItem({ status: 'applied' })] }))).toBe(false);
  });

  it('shows the plan before the event and its promises when «До события» is on', () => {
    // До срочной заявки план совпадал с утренним у всех, кроме 18754: её бригада стала недоступна.
    const before = callList(state(), {}, callContext(state(), true, '13:00'));
    expect(ids(before.pending)).toEqual(['18754']);
  });

  it('takes the promise to remember from the current plan', () => {
    expect(plannedPromise(makePlanningState().plan, '50104', 4)).toEqual({ start: '14:00', engineer_id: 'E01', version: 4 });
    expect(plannedPromise(makePlanningState().plan, '18754', 4)).toEqual({ start: null, engineer_id: null, version: 4 });
  });
});
