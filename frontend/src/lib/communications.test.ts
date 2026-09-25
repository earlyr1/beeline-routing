import { describe, expect, it } from 'vitest';
import type { HHMM, PlanningState } from '../api/types';
import { makeAsapRequest, makeAsapState, makePlanningState, makeTimelineItem, WINDOW_GRID } from '../test/fixtures';
import {
  agreedWindow,
  agreementEvent,
  callAgreedText,
  callChangeText,
  callList,
  eventsAhead,
  type AgreedMarks,
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
        // Переехавший визит — не начатая работа: закрепления прежнего времени у него нет.
        visits: route.visits.map((visit) => (visit.request_id === requestId ? { ...visit, start, end, pinned: false } : visit)),
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

/** Сказали, что сегодня не приедем: звонок на шкале перенёс заявку, и визита у неё больше нет. */
const postponed =
  (requestId: string): Change =>
  (state) => ({
    ...withoutVisit(requestId)(state),
    requests: state.requests.map((request) => (request.id === requestId ? { ...request, status: 'postponed' } : request)),
  });

/** Утром визит заявки стоял в другое время: клиенту его не называли, окно от этого не меняется. */
const withMorningVisit =
  (requestId: string, start: HHMM): Change =>
  (state) => ({
    ...state,
    morning: state.morning.map((item) => (item.request_id === requestId ? { ...item, start } : item)),
  });

/** Список звонков по текущему плану; на часах фикстуры 13:00, сетка окон — та, что отдаёт сервер. */
const calls = (planning: PlanningState, agreed: AgreedMarks = {}, clock: HHMM = '13:00') =>
  callList(planning, agreed, clock, WINDOW_GRID);

/**
 * Договорённость с сервера: событие «Коммуникация» на шкале. Названное окно оно уже сделало окном заявки,
 * поэтому день к ней — с этим окном (withWindow).
 */
const told = (requestId: string, window: { start: HHMM; end: HHMM } | null): AgreedMarks => ({
  [requestId]: { window: window && { ...window, asap: false }, entry_id: 'tl_9', applied: true },
});

/** Отметка, чьё событие ещё в пути: окна заявки оно пока не поменяло, снять её нечем. */
const inFlight = (requestId: string, window: { start: HHMM; end: HHMM } | null): AgreedMarks => ({
  [requestId]: { window: window && { ...window, asap: false }, entry_id: null, applied: false },
});

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
    // Звонок на шкале сделал названное окно окном заявки: дальше расхождения считаются от него.
    const moved = day(withWindow('86160', '16:00', '18:00'), withVisit('86160', '16:30', '17:30'));
    const settled = calls(moved, told('86160', { start: '16:00', end: '18:00' }));
    expect(ids(settled.pending)).toEqual(['18754']);
    expect(settled.agreed.map(callAgreedText)).toEqual(['договорились на окно 16:00–18:00']);
    // Строка знает своё событие шкалы: удалить его — снять отметку.
    expect(settled.agreed[0].entryId).toBe('tl_9');

    // Следующее событие увезло окно ещё раз: клиент знает 16:00–18:00, строка возвращается.
    const again = day(withWindow('86160', '19:00', '21:00'), withVisit('86160', '19:30', '20:30'));
    const back = calls(again, told('86160', { start: '16:00', end: '18:00' }));
    expect(callChangeText(row(back.pending, '86160'))).toBe('окно было 16:00–18:00 → стало 19:00–21:00');
  });

  it('settles a broken window by the window named on the phone and calls again when the visit leaves it', () => {
    // Окно заявки 12:00–14:00 план не выполняет: диспетчер назвал клиенту слот 14:00–16:00 и отметил «Согласовано».
    const late = day(withVisit('86160', '15:10', '16:10'));
    const named = agreedWindow(late, '86160', WINDOW_GRID);
    expect(named).toEqual({ start: '14:00', end: '16:00', asap: false });

    // Пока событие в пути, окно заявки прежнее, а строка уже внизу: диспетчер положил трубку.
    const sending = calls(late, inFlight('86160', named));
    expect(ids(sending.pending)).toEqual(['18754']);
    expect(sending.agreed).toEqual([expect.objectContaining({ requestId: '86160', entryId: null })]);

    // Сервер применил звонок: окно заявки теперь 14:00–16:00.
    const agreed = (...changes: Change[]) => day(withWindow('86160', '14:00', '16:00'), ...changes);
    const settled = calls(agreed(withVisit('86160', '15:10', '16:10')), told('86160', named));
    expect(ids(settled.pending)).toEqual(['18754']);
    expect(settled.agreed.map(callAgreedText)).toEqual(['договорились на окно 14:00–16:00']);

    // Внутри названного окна визит ходит молча: клиенту обещали окно, а не минуту.
    expect(ids(calls(agreed(withVisit('86160', '15:40', '16:40')), told('86160', named)).pending)).toEqual(['18754']);

    // А из него уехал — звоним снова и называем следующий слот.
    const back = calls(agreed(withVisit('86160', '17:30', '18:30')), told('86160', named));
    expect(callChangeText(row(back.pending, '86160'))).toBe('не попадаем в окно 14:00–16:00 — назовите окно 16:00–18:00');
  });

  it('keeps a postponed request among the agreed ones: the client does not wait for a call', () => {
    const list = calls(day(postponed('18754')), told('18754', null));
    expect(ids(list.pending)).toEqual([]);
    expect(list.agreed.map(callAgreedText)).toEqual(['сказали, что сегодня не приедем']);

    // Пока звонок в пути, строка тоже внизу: заявка ещё без визита, и сказали ровно это.
    expect(ids(calls(day(), inFlight('18754', null)).agreed)).toEqual(['18754']);

    // Визит вернулся в план, пока звонок был в пути: клиент ждёт, что сегодня никто не приедет, и должен узнать окно.
    const returned = calls(day(withReturnedVisit('18754', 'E01', '18:00', '19:00')), inFlight('18754', null));
    expect(row(returned.pending, '18754')).toMatchObject({ kind: 'window', severity: 'yellow', known: null });
    expect(callChangeText(row(returned.pending, '18754'))).toBe(
      'сказали, что сегодня не приедем → приедем в окно 18:00–20:00',
    );
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
    const returned = row(calls(emergency, inFlight('86160', null)).pending, '86160');
    expect(returned.promise).toEqual({ start: '14:00', end: '16:00', asap: false });
    expect(callChangeText(returned)).toBe('сказали, что сегодня не приедем → приедем в окно 14:00–16:00');

    // «Согласовано» называет тот же слот, и строка после него уходит вниз, а не возвращается.
    const named = agreedWindow(emergency, '86160', WINDOW_GRID);
    expect(named).toEqual({ start: '14:00', end: '16:00', asap: false });
    expect(calls(emergency, inFlight('86160', named)).agreed.map(callAgreedText)).toEqual(['договорились на окно 14:00–16:00']);
    const applied = day(withWindow('86160', '14:00', '16:00'), withVisit('86160', '15:10', '16:10'));
    expect(calls(applied, told('86160', named)).agreed.map(callAgreedText)).toEqual(['договорились на окно 14:00–16:00']);
  });

  it('lets go of the work that has already started: the brigade is at the client', () => {
    // До начала визита диспетчер называет слот вокруг него как обычно.
    const late = day(withVisit('46393', '13:50', '15:00'));
    expect(callChangeText(row(calls(late, {}, '13:45').pending, '46393'))).toBe(
      'не попадаем в окно 15:00–17:00 — назовите окно 12:00–14:00',
    );
    // Работа началась: называть окно поздно, а звонок о ней сервер не примет — строки нет, хотя визит ещё идёт.
    expect(ids(calls(late, {}, '13:55').pending)).not.toContain('46393');
    // Закреплённая работа тоже начатая, какое бы время ни стояло на часах.
    const pinned = withRoutes(late, late.plan.routes.map((route) => ({
      ...route,
      visits: route.visits.map((visit) => (visit.request_id === '46393' ? { ...visit, pinned: true } : visit)),
    })));
    expect(ids(calls(pinned, {}, '13:45').pending)).not.toContain('46393');
    // С клиентом уже договорились — отметка остаётся на месте до конца визита, её ещё можно снять.
    const agreed = told('46393', { start: '12:00', end: '14:00' });
    const settled = day(withWindow('46393', '12:00', '14:00'), withVisit('46393', '13:50', '15:00'));
    expect(ids(calls(settled, agreed, '14:20').agreed)).toEqual(['46393']);
  });

  it('sorts the calls: the broken promises first, then by the time of the day', () => {
    const list = calls(day(withVisit('86160', '15:10', '16:10'), withWindow('46393', '15:00', '16:00')));
    expect(ids(list.pending)).toEqual(['86160', '18754', '46393']);
    expect(list.pending.map((item) => item.severity)).toEqual(['red', 'red', 'yellow']);
  });

  it('lets go of the visits that are already over', () => {
    // К девяти вечера все визиты плана закончились: звонить остаётся только тому, к кому сегодня не приедут.
    expect(ids(calls(day(), {}, '21:00').pending)).toEqual(['18754']);
    // А до начала визита строка на месте: 86160 не успевает в окно и начнётся в 15:10.
    expect(ids(calls(day(withVisit('86160', '15:10', '16:10')), {}, '15:05').pending)).toContain('86160');
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

  it('sees the events the clock has not reached yet', () => {
    expect(eventsAhead(day())).toBe(false);
    expect(eventsAhead(makePlanningState({ timeline: [makeTimelineItem({ status: 'pending' })] }))).toBe(true);
    expect(eventsAhead(makePlanningState({ timeline: [makeTimelineItem({ status: 'applied' })] }))).toBe(false);
  });

  it('takes the window to name from the current plan', () => {
    expect(agreedWindow(day(), '50104', WINDOW_GRID)).toEqual({ start: '14:00', end: '16:00', asap: false });
    // Визит вне окна заявки: клиенту называют слот вокруг визита, и звонок сделает его окном заявки.
    expect(agreedWindow(day(withVisit('86160', '15:10', '16:10')), '86160', WINDOW_GRID)).toEqual({
      start: '14:00',
      end: '16:00',
      asap: false,
    });
    // Визита нет: клиенту говорят, что сегодня не приедем.
    expect(agreedWindow(day(), '18754', WINDOW_GRID)).toBeNull();
  });

  it('makes the call an event of the timeline at the time on the clock', () => {
    expect(agreementEvent('86160', { start: '14:00', end: '16:00', asap: false }, '13:05')).toEqual({
      type: 'client_agreed',
      time: '13:05',
      request: null,
      request_id: '86160',
      engineer_id: null,
      agreed_window: { start: '14:00', end: '16:00', asap: false },
    });
    expect(agreementEvent('18754', null, '13:05').agreed_window).toBeNull();
  });
});
