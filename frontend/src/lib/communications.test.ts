import { describe, expect, it } from 'vitest';
import type { HHMM, PlanningState } from '../api/types';
import { makeAsapRequest, makeAsapState, makePlanningState, makeTimelineItem, WINDOW_GRID } from '../test/fixtures';
import {
  agreedWindow,
  callAgreedText,
  callChangeText,
  callList,
  eventsAhead,
  type AgreedWindows,
} from './communications';

/** Правка дня фикстуры: их можно складывать, как складываются события дня. */
type Change = (state: PlanningState) => PlanningState;

/** День фикстуры с правками: на часах 13:00, 50104 уехала к Арташкину, 18754 осталась без бригады. */
const day = (...changes: Change[]) => changes.reduce((state, change) => change(state), makePlanningState());

const withRoutes = (state: PlanningState, routes: PlanningState['plan']['routes']) => ({
  ...state,
  plan: { ...state.plan, routes },
});

/** Визит заявки переехал на другое время. */
const withVisit =
  (requestId: string, start: HHMM, end: HHMM): Change =>
  (state) =>
    withRoutes(
      state,
      state.plan.routes.map((route) => ({
        ...route,
        visits: route.visits.map((visit) => (visit.request_id === requestId ? { ...visit, start, end } : visit)),
      })),
    );

/** Заявка осталась без визита: сегодня к клиенту не приедут. */
const withoutVisit =
  (requestId: string): Change =>
  (state) =>
    withRoutes(
      state,
      state.plan.routes.map((route) => ({
        ...route,
        visits: route.visits.filter((visit) => visit.request_id !== requestId),
      })),
    );

/** Заявка уехала к другой бригаде, время визита прежнее. */
const withBrigade =
  (requestId: string, engineerId: string): Change =>
  (state) => {
    const moved = state.plan.routes.flatMap((route) => route.visits).find((visit) => visit.request_id === requestId)!;
    return withRoutes(
      state,
      state.plan.routes.map((route) => ({
        ...route,
        visits:
          route.engineer_id === engineerId
            ? [...route.visits.filter((visit) => visit.request_id !== requestId), moved]
            : route.visits.filter((visit) => visit.request_id !== requestId),
      })),
    );
  };

/** Заявка без визита снова попала в маршрут бригады. */
const withReturnedVisit =
  (requestId: string, engineerId: string, start: HHMM, end: HHMM): Change =>
  (state) =>
    withRoutes(
      state,
      state.plan.routes.map((route) =>
        route.engineer_id === engineerId
          ? {
              ...route,
              visits: [
                ...route.visits,
                { request_id: requestId, arrival: start, start, end, leg_km: 4, leg_min: 20, late_min: 0, pinned: false },
              ],
            }
          : route,
      ),
    );

/** Диспетчер подвинул окно заявки среди дня: утреннее окно от этого не меняется. */
const withWindow =
  (requestId: string, start: HHMM, end: HHMM): Change =>
  (state) => ({
    ...state,
    requests: state.requests.map((request) =>
      request.id === requestId ? { ...request, window_start: start, window_end: end } : request,
    ),
  });

/** Утром визит заявки стоял в другое время: клиенту его не называли, окно от этого не меняется. */
const withMorningVisit =
  (requestId: string, start: HHMM): Change =>
  (state) => ({
    ...state,
    morning: state.morning.map((item) => (item.request_id === requestId ? { ...item, start } : item)),
  });

/** Список звонков по текущему плану; на часах фикстуры 13:00, сетка окон — та, что отдаёт сервер. */
const calls = (planning: PlanningState, agreed: AgreedWindows = {}, clock: HHMM = '13:00') =>
  callList(planning, agreed, clock, WINDOW_GRID);

const ids = (rows: { requestId: string }[]) => rows.map((row) => row.requestId);
const row = <T extends { requestId: string }>(rows: T[], requestId: string): T =>
  rows.find((item) => item.requestId === requestId)!;

describe('callList', () => {
  it('says nothing about a visit that moved inside the window the client knows or went to another brigade', () => {
    // Утром визит стоял в 12:30, теперь в 13:15 — клиенту называли окно 12:00–14:00, и оно выполняется.
    const inside = day(withMorningVisit('86160', '12:30'), withVisit('86160', '13:15', '14:15'));
    expect(ids(calls(inside).pending)).not.toContain('86160');

    // 50104 утром была у Белузина, теперь у Арташкина в то же время: клиенту бригаду не обещали.
    expect(ids(calls(day()).pending)).not.toContain('50104');
    expect(ids(calls(day(withBrigade('46393', 'E02'))).pending)).not.toContain('46393');

    // Во всём дне фикстуры звонок один: к 18754 сегодня не приедут.
    expect(ids(calls(day()).pending)).toEqual(['18754']);
  });

  it('calls red when the visit misses the window the client knows and offers a window to name instead', () => {
    const late = day(withVisit('86160', '15:10', '16:10'));
    expect(row(calls(late).pending, '86160')).toMatchObject({
      kind: 'outside',
      severity: 'red',
      known: { start: '12:00', end: '14:00' },
      missed: { start: '12:00', end: '14:00' },
      // Новое окно — слот сетки, в который попал визит: минуту плана клиенту не называют.
      promise: { start: '14:00', end: '16:00' },
      start: '15:10',
      engineerId: 'E01',
    });
    expect(callChangeText(row(calls(late).pending, '86160'))).toBe(
      'не попадаем в окно 12:00–14:00 — назовите окно 14:00–16:00',
    );

    // Раньше начала окна — то же самое: клиента в это время может не быть дома, и окно ему называют другое.
    const early = day(withVisit('46393', '14:00', '14:45'));
    expect(row(calls(early).pending, '46393')).toMatchObject({ kind: 'outside', severity: 'red' });
    expect(callChangeText(row(calls(early).pending, '46393'))).toBe(
      'не попадаем в окно 15:00–17:00 — назовите окно 14:00–16:00',
    );
  });

  it('calls red when the client is left without a visit today', () => {
    // Бригада Комарь стала недоступна, и 18754 осталась без визита: клиенту обещали окно 18:00–20:00.
    expect(row(calls(day()).pending, '18754')).toMatchObject({
      kind: 'lost',
      severity: 'red',
      known: { start: '18:00', end: '20:00' },
      promise: null,
      start: null,
      engineerId: null,
    });
    // В строке видно, какое окно срывается: звонить, не зная обещания, диспетчеру нечего.
    expect(callChangeText(row(calls(day()).pending, '18754'))).toBe('сегодня не приедем, обещали окно 18:00–20:00');

    // Заявку сняли с маршрута среди дня: звонок такой же.
    expect(row(calls(day(withoutVisit('50104'))).pending, '50104')).toMatchObject({ kind: 'lost', severity: 'red' });
    // Отменённая 10135 в список не попадает: клиент уже знает.
    expect(ids(calls(day()).pending)).not.toContain('10135');
  });

  it('calls yellow when the window itself moved and the client must learn the new one', () => {
    const moved = day(withWindow('86160', '16:00', '18:00'), withVisit('86160', '16:30', '17:30'));
    expect(row(calls(moved).pending, '86160')).toMatchObject({
      kind: 'window',
      severity: 'yellow',
      known: { start: '12:00', end: '14:00' },
      promise: { start: '16:00', end: '18:00' },
      start: '16:30',
    });
    expect(callChangeText(row(calls(moved).pending, '86160'))).toBe('окно было 12:00–14:00 → стало 16:00–18:00');

    // Новое окно диспетчер поставил, а план и в него не попадает: называть его нельзя, строка красная.
    const missed = day(withWindow('86160', '16:00', '18:00'), withVisit('86160', '18:30', '19:30'));
    expect(row(calls(missed).pending, '86160')).toMatchObject({ kind: 'outside', severity: 'red' });
    expect(callChangeText(row(calls(missed).pending, '86160'))).toBe(
      'не попадаем в окно 16:00–18:00 — назовите окно 18:00–20:00',
    );

    // Окно сузили, и визит выпал уже из него: клиенту про это узкое окно говорить нечего.
    const narrowed = day(withWindow('86160', '12:00', '13:00'), withVisit('86160', '13:30', '14:30'));
    expect(callChangeText(row(calls(narrowed).pending, '86160'))).toBe(
      'не попадаем в окно 12:00–13:00 — назовите окно 12:00–14:00',
    );
  });

  it('keeps an agreed window out of the calls and brings the row back when the window moves again', () => {
    const agreedOn = (start: HHMM, end: HHMM): AgreedWindows => ({
      '86160': { window: { start, end }, requestWindow: { start, end }, version: 4 },
    });
    const moved = day(withWindow('86160', '16:00', '18:00'), withVisit('86160', '16:30', '17:30'));
    const settled = calls(moved, agreedOn('16:00', '18:00'));
    expect(ids(settled.pending)).toEqual(['18754']);
    expect(settled.agreed.map(callAgreedText)).toEqual(['договорились на окно 16:00–18:00']);

    // Следующее событие увезло окно ещё раз: клиент знает 16:00–18:00, строка возвращается.
    const again = day(withWindow('86160', '19:00', '21:00'), withVisit('86160', '19:30', '20:30'));
    const back = calls(again, agreedOn('16:00', '18:00'));
    expect(callChangeText(row(back.pending, '86160'))).toBe('окно было 16:00–18:00 → стало 19:00–21:00');
  });

  it('settles a broken window by the window named on the phone and calls again when the visit leaves it', () => {
    // Окно заявки 12:00–14:00 план не выполняет: диспетчер назвал клиенту слот 14:00–16:00 и отметил «Согласовано».
    const late = day(withVisit('86160', '15:10', '16:10'));
    const told: AgreedWindows = { '86160': agreedWindow(late, '86160', WINDOW_GRID) };
    expect(told['86160']).toMatchObject({ window: { start: '14:00', end: '16:00' } });

    const settled = calls(late, told);
    expect(ids(settled.pending)).toEqual(['18754']);
    expect(settled.agreed.map(callAgreedText)).toEqual(['договорились на окно 14:00–16:00']);

    // Внутри названного окна визит ходит молча: клиенту обещали окно, а не минуту.
    expect(ids(calls(day(withVisit('86160', '15:40', '16:40')), told).pending)).toEqual(['18754']);

    // А из него уехал — звоним снова и называем следующий слот.
    const back = calls(day(withVisit('86160', '17:30', '18:30')), told);
    expect(callChangeText(row(back.pending, '86160'))).toBe('не попадаем в окно 14:00–16:00 — назовите окно 16:00–18:00');
  });

  it('remembers an agreed «сегодня не приедем» and calls again when the visit comes back', () => {
    const told: AgreedWindows = { '18754': { window: null, version: 4 } };
    const list = calls(day(), told);
    expect(ids(list.pending)).toEqual([]);
    expect(list.agreed.map(callAgreedText)).toEqual(['сказали, что сегодня не приедем']);

    // Визит вернулся в план: клиент ждёт, что сегодня никто не приедет, и должен узнать окно.
    const returned = calls(day(withReturnedVisit('18754', 'E01', '18:00', '19:00')), told);
    expect(row(returned.pending, '18754')).toMatchObject({ kind: 'window', severity: 'yellow', known: null });
    expect(callChangeText(row(returned.pending, '18754'))).toBe(
      'сказали, что сегодня не приедем → приедем в окно 18:00–20:00',
    );
  });

  it('does not turn an agreed window around when the clock goes back behind the events', () => {
    // Диспетчер договорился на плане версии 5, потом отмотал часы: на экране план версии 4.
    const ahead: AgreedWindows = { '18754': { window: null, version: 5 } };
    const rewound = calls(day(), ahead);
    expect(ids(rewound.pending)).not.toContain('18754');
    expect(ids(rewound.agreed)).not.toContain('18754');

    // Часы вернулись к тому же плану: отметка снова сравнивается, и звонить не о чем.
    expect(ids(calls(day(), { '18754': { window: null, version: 4 } }).agreed)).toEqual(['18754']);
  });

  it('names a slot of the grid, never an interval of its own', () => {
    // Куда бы ни уехал визит, названное окно — слот сетки: диспетчер предлагает клиенту слот.
    for (const [start, end, slot] of [
      // Визит раньше рабочего дня и визит за его концом своего слота не имеют: называют ближайший.
      ['09:30', '10:30', '10:00–12:00'],
      ['15:10', '16:10', '14:00–16:00'],
      ['17:45', '18:45', '16:00–18:00'],
      ['21:50', '22:50', '20:00–22:00'],
      ['22:30', '23:30', '20:00–22:00'],
    ] as const) {
      const promise = row(calls(day(withVisit('86160', start, end)), {}, '00:00').pending, '86160').promise!;
      expect(`${promise.start}–${promise.end}`).toBe(slot);
      expect(WINDOW_GRID).toContainEqual({ start: promise.start, end: promise.end });
    }

    // Без сетки от сервера (старый сервер, офлайн) окно прежнее: получасовая отметка перед визитом.
    const late = day(withVisit('86160', '15:10', '16:10'));
    expect(callList(late, {}, '13:00').pending.find((call) => call.requestId === '86160')?.promise).toEqual({
      start: '15:00',
      end: '17:00',
      asap: false,
    });
  });

  it('offers a slot even when the request carries a window that is not one', () => {
    // У аварии выгрузки окно 00:01–23:59 — пометка данных, а не обещание клиенту: называют слот вокруг визита.
    const emergency = day(withWindow('86160', '00:01', '23:59'), withVisit('86160', '15:10', '16:10'));
    const told: AgreedWindows = { '86160': { window: null, version: 4 } };
    const returned = row(calls(emergency, told).pending, '86160');
    expect(returned.promise).toEqual({ start: '14:00', end: '16:00', asap: false });
    expect(callChangeText(returned)).toBe('сказали, что сегодня не приедем → приедем в окно 14:00–16:00');

    // «Согласовано» запоминает тот же слот, и строка после него уходит вниз, а не возвращается.
    const mark: AgreedWindows = { '86160': agreedWindow(emergency, '86160', WINDOW_GRID) };
    expect(mark['86160']).toMatchObject({ window: { start: '14:00', end: '16:00' } });
    expect(calls(emergency, mark).agreed.map(callAgreedText)).toEqual(['договорились на окно 14:00–16:00']);
  });

  it('does not name a window that ran out while the brigade was already at the client', () => {
    // Визит 13:50–15:00 ещё идёт, а слот вокруг его начала (12:00–14:00) к 14:20 уже кончился.
    const late = day(withVisit('46393', '13:50', '15:00'));
    const underway = row(calls(late, {}, '14:20').pending, '46393');
    expect(underway).toMatchObject({ kind: 'outside', underway: true, promise: { start: '12:00', end: '14:00' } });
    expect(callChangeText(underway)).toBe('не попадаем в окно 15:00–17:00 — бригада уже у клиента');

    // Названное окно всё равно держит начало визита: после «Согласовано» строка уходит, а не возвращается навсегда.
    const told: AgreedWindows = { '46393': agreedWindow(late, '46393', WINDOW_GRID) };
    expect(ids(calls(late, told, '14:20').pending)).not.toContain('46393');

    // Пока слот идёт, диспетчер называет его как обычно.
    expect(callChangeText(row(calls(late, {}, '13:55').pending, '46393'))).toBe(
      'не попадаем в окно 15:00–17:00 — назовите окно 12:00–14:00',
    );
  });

  it('sorts the calls: the broken promises first, then by the time of the day', () => {
    const list = calls(day(withVisit('86160', '15:10', '16:10'), withWindow('46393', '15:00', '16:00')));
    expect(ids(list.pending)).toEqual(['86160', '18754', '46393']);
    expect(list.pending.map((item) => item.severity)).toEqual(['red', 'red', 'yellow']);
  });

  it('lets go of the visits that are already over', () => {
    // К девяти вечера все визиты плана закончились: звонить остаётся только тому, к кому сегодня не приедут.
    expect(ids(calls(day(), {}, '21:00').pending)).toEqual(['18754']);
    // И до конца визита строка на месте: 86160 не успевает в окно и идёт до 16:10.
    expect(ids(calls(day(withVisit('86160', '15:10', '16:10')), {}, '15:30').pending)).toContain('86160');
  });

  it('takes the window of a request accepted during the day from the event that brought it in', () => {
    // Срочной заявки утром не было: клиент услышал окно, когда её приняли, и план в него попадает.
    expect(ids(calls(day()).pending)).not.toContain('URG-001');
    expect(row(calls(day(withVisit('URG-001', '15:30', '16:30'))).pending, 'URG-001')).toMatchObject({
      kind: 'outside',
      severity: 'red',
      label: 'URG-001',
    });

    // Окно такой заявки правит тот же диспетчер: клиент знает то, с которым её приняли, и должен узнать новое.
    const moved = day(withWindow('URG-001', '14:00', '16:00'), withVisit('URG-001', '14:10', '15:10'));
    expect(row(calls(moved).pending, 'URG-001')).toMatchObject({
      kind: 'window',
      severity: 'yellow',
      known: { start: '13:00', end: '15:00' },
    });
    expect(callChangeText(row(calls(moved).pending, 'URG-001'))).toBe('окно было 13:00–15:00 → стало 14:00–16:00');
  });

  it('calls a request «как можно скорее» by its own words, not by the raw pair of times', () => {
    const state = makeAsapState();
    const asapDay: PlanningState = {
      ...state,
      // Заявку приняли среди дня как «как можно скорее с 13:00», а потом диспетчер назначил ей окно.
      events: [
        ...state.events,
        {
          id: 'ev_4',
          event: { type: 'urgent', time: '13:00', request: makeAsapRequest(), request_id: null, engineer_id: null },
          version: 5,
        },
      ],
      requests: state.requests.map((request) =>
        request.id === 'URG-002' ? { ...request, window_start: '14:00', window_end: '16:00', asap: false } : request,
      ),
    };
    expect(callChangeText(row(calls(asapDay).pending, 'URG-002'))).toBe('как можно скорее с 13:00 → окно 14:00–16:00');
  });

  it('does not fall over a mark of another shape left in the browser storage', () => {
    // Ключ отметок новый, но в хранилище может лежать что угодно: список звонков считается ещё и для бейджа вкладки.
    expect(() => calls(day(), { '50104': { start: '14:00' } } as unknown as AgreedWindows)).not.toThrow();
  });

  it('sees the events the clock has not reached yet', () => {
    expect(eventsAhead(day())).toBe(false);
    expect(eventsAhead(makePlanningState({ timeline: [makeTimelineItem({ status: 'pending' })] }))).toBe(true);
    expect(eventsAhead(makePlanningState({ timeline: [makeTimelineItem({ status: 'applied' })] }))).toBe(false);
  });

  it('takes the window to remember from the current plan', () => {
    expect(agreedWindow(day(), '50104', WINDOW_GRID)).toMatchObject({
      window: { start: '14:00', end: '16:00' },
      requestWindow: { start: '14:00', end: '16:00' },
      version: 4,
    });
    // Визит вне окна заявки: клиенту назвали новое окно, а окно самой заявки отметка помнит отдельно.
    expect(agreedWindow(day(withVisit('86160', '15:10', '16:10')), '86160', WINDOW_GRID)).toMatchObject({
      window: { start: '14:00', end: '16:00' },
      requestWindow: { start: '12:00', end: '14:00' },
    });
    // Визита нет: отметка значит «сказали, что сегодня не приедем».
    expect(agreedWindow(day(), '18754', WINDOW_GRID)).toMatchObject({ window: null, version: 4 });
  });
});
