import { describe, expect, it } from 'vitest';
import { makePlanningState } from '../test/fixtures';
import {
  assignmentIndex,
  byId,
  diffBadge,
  diffMarks,
  displayedPlan,
  routeRequestIds,
  routeRows,
  routeSummary,
  sortRequestsForList,
  straightLegs,
  unassignedIndex,
} from './planView';

describe('planView', () => {
  const state = makePlanningState();

  it('shows the previous plan only when requested and available', () => {
    expect(displayedPlan(state, false)).toBe(state.plan);
    expect(displayedPlan(state, true)).toBe(state.previous_plan);
    const noPrevious = { ...state, previous_plan: null };
    expect(displayedPlan(noPrevious, true)).toBe(noPrevious.plan);
  });

  it('indexes assignments with visit order and unassigned reasons', () => {
    const index = assignmentIndex(state.plan);
    expect(index.get('50104')).toMatchObject({ engineerId: 'E01', order: 2 });
    expect(index.has('18754')).toBe(false);
    expect(unassignedIndex(state.plan).get('18754')?.reason_code).toBe('does_not_fit_window_or_shift');
  });

  it('marks diff with added before moved before time shifts', () => {
    const marks = diffMarks(state.last_diff);
    expect(marks.get('URG-001')).toBe('added');
    expect(marks.get('50104')).toBe('moved');
    expect(marks.get('46393')).toBe('shifted');
    expect(diffMarks(null).size).toBe(0);
  });

  it('builds straight legs from the engineer start through visits', () => {
    const legs = straightLegs(state.plan.routes[1], state.engineers[1], byId(state.requests));
    expect(legs.map((leg) => leg.to_request_id)).toEqual(['84627', 'URG-001']);
    expect(legs[0].coordinates[0]).toEqual([state.engineers[1].start_lon, state.engineers[1].start_lat]);
    expect(legs[0].coordinates[0]).not.toEqual([state.office.lon, state.office.lat]);
    expect(legs[1].coordinates[0]).toEqual([37.805, 55.76]);
  });

  it('sorts the list: assigned by start, then unassigned, cancelled last', () => {
    const ids = sortRequestsForList(state.requests, state.plan).map((request) => request.id);
    expect(ids).toEqual(['74198', '84627', '86160', 'URG-001', '50104', '46393', '18754', '10135']);
  });

  it('labels diff badges with engineers and with the upcoming change in the plan before the event', () => {
    const engineers = byId(state.engineers);
    expect(diffBadge('moved', '50104', state.last_diff, engineers, false)).toEqual({
      text: 'Перенесена от «Бригада Белузин»',
      title: 'Перенос от «Бригада Белузин» к «Бригада Арташкин»',
    });
    expect(diffBadge('moved', '50104', state.last_diff, engineers, true)).toEqual({
      text: 'Будет перенесена к «Бригада Арташкин»',
      title: 'Перенос от «Бригада Белузин» к «Бригада Арташкин»',
    });
    expect(diffBadge('added', 'URG-001', state.last_diff, engineers, false)).toEqual({ text: 'Новое назначение' });
    expect(diffBadge('added', 'URG-001', state.last_diff, engineers, true)).toEqual({ text: 'Будет назначена' });
    expect(diffBadge('shifted', '46393', state.last_diff, engineers, true)).toEqual({ text: 'Сдвинется время' });
    expect(diffBadge('removed', '10135', state.last_diff, engineers, true)).toEqual({ text: 'Будет снята' });
  });

  it('summarises a route for the dispatcher', () => {
    const summary = routeSummary(state, state.plan, 'E02');
    expect(summary).toMatchObject({ totalKm: 11.2, travelMin: 50, endOfWork: '14:05', equipmentCount: 0 });
    // Оборудование считаем по визитам маршрута: у Арташкина это 74198 и 86160.
    expect(routeSummary(state, state.plan, 'E01')?.equipmentCount).toBe(2);
    expect(summary?.stops.map((stop) => [stop.visit.request_id, stop.slackMin])).toEqual([
      ['84627', 120],
      ['URG-001', 115],
    ]);
    expect(summary?.sentences).toEqual([
      'Маршрут 11,2 км — 32% пробега всего плана; порядок визитов следует окнам заявок.',
      'Все визиты начинаются внутри окон, минимальный запас до конца окна 1 ч 55 мин (заявка URG-001).',
      'Работы заканчиваются в 14:05, до конца смены в 22:00 остаётся 7 ч 55 мин.',
      'Визит 84627 начат до события и закреплён: перепланирование его не меняет.',
    ]);
    expect(routeSummary(state, state.plan, 'NOPE')).toBeNull();
  });

  it('explains late visits, an order that differs from windows and work past the shift', () => {
    const [e01, ...rest] = state.plan.routes;
    const visits = [
      e01.visits[0],
      e01.visits[1],
      e01.visits[3],
      { ...e01.visits[2], arrival: '16:05', start: '16:10', end: '16:55', late_min: 10 },
    ];
    const plan = { ...state.plan, routes: [{ ...e01, visits }, ...rest] };
    const engineers = state.engineers.map((engineer) => (engineer.id === 'E01' ? { ...engineer, shift_end: '16:30' } : engineer));
    const summary = routeSummary({ ...state, engineers, plan }, plan, 'E01');
    expect(summary?.endOfWork).toBe('16:55');
    expect(summary?.sentences.slice(0, 3)).toEqual([
      'Маршрут 23,7 км — 68% пробега всего плана; порядок отличается от порядка окон: визиты с пересекающимися окнами расставлены так, чтобы сократить переезды.',
      'С опозданием к окну: 50104 на 10 мин.',
      'Работы заканчиваются в 16:55, позже конца смены в 16:30.',
    ]);
  });

  it('puts the lunch between the visits in time order or after the last visit', () => {
    const before = routeSummary(state, state.previous_plan!, 'E01');
    expect(before?.lunch).toEqual({ start: '13:30', end: '14:15' });
    const rows = (summary: ReturnType<typeof routeSummary>) =>
      routeRows(summary!).map((row) => (row.kind === 'lunch' ? `Обед ${row.lunch.start}` : row.stop.visit.request_id));
    expect(rows(before)).toEqual(['74198', '86160', 'Обед 13:30', '46393']);
    expect(rows(routeSummary(state, state.plan, 'E01'))).toEqual(['74198', '86160', '50104', '46393', 'Обед 15:55']);
    expect(routeSummary(state, state.plan, 'E03')?.lunch).toBeNull();
    expect(rows(routeSummary(state, state.control!, 'E01'))).toEqual(['74198', '86160']);
  });

  it('returns route order for one engineer', () => {
    expect(routeRequestIds(state.plan, 'E01')).toEqual(['74198', '86160', '50104', '46393']);
    expect(routeRequestIds(state.plan, 'E03')).toEqual([]);
  });
});
