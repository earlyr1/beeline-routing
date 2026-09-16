import { describe, expect, it } from 'vitest';
import { makeAsapState, makePlanningState, makeTimelineItem } from '../test/fixtures';
import { dayScale, hourTicks, percent, timelineRow, timelineRows, timeScale } from './timeline';

describe('timeline', () => {
  const state = makePlanningState();

  it('uses the default 09:00–23:00 axis when data fits inside it', () => {
    expect(timeScale(state, state.plan)).toEqual({ from: 540, to: 1380 });
  });

  it('extends the axis to fit data but never beyond 00:00–24:00', () => {
    const early = { ...state, engineers: state.engineers.map((engineer) => ({ ...engineer, shift_start: '06:30' })) };
    expect(timeScale(early, state.plan).from).toBe(360);

    // Смена, начатая между 08:00 и 09:00, тоже сдвигает начало шкалы вниз, до целого часа.
    const morning = { ...state, engineers: state.engineers.map((engineer) => ({ ...engineer, shift_start: '08:30' })) };
    expect(timeScale(morning, state.plan).from).toBe(480);

    const route = state.plan.routes[0];
    const overnight = {
      ...state.plan,
      routes: [{ ...route, visits: [{ ...route.visits[0], start: '23:10', end: '49:13' }] }, ...state.plan.routes.slice(1)],
    };
    expect(timeScale(state, overnight)).toEqual({ from: 540, to: 1440 });
  });

  it('converts minutes to clamped percentages', () => {
    const scale = { from: 540, to: 1320 };
    expect(percent(scale, 540)).toBe(0);
    expect(percent(scale, 930)).toBe(50);
    expect(percent(scale, 2000)).toBe(100);
    expect(percent(scale, 0)).toBe(0);
  });

  it('builds rows with bars, windows, pinned flags and unavailability', () => {
    const scale = timeScale(state, state.plan);
    const rows = timelineRows(state, state.plan, scale);
    expect(rows[0].bars.map((bar) => bar.requestId)).toEqual(['74198', '86160', '50104', '46393']);
    expect(rows[0].bars[0].pinned).toBe(true);
    expect(rows[0].bars[0].clipped).toBe(false);
    expect(rows[0].bars[2].windowLeft).toBeCloseTo(percent(scale, 840));
    expect(rows[1].bars[1].urgent).toBe(true);
    expect(rows[0].unavailableLeft).toBeNull();
    expect(rows[2].unavailableLeft).toBeCloseTo(percent(scale, 780));
  });

  it('builds the personal row of one engineer exactly like the row in the whole timeline', () => {
    const scale = timeScale(state, state.plan);
    const rows = timelineRows(state, state.plan, scale);
    expect(timelineRow(state, state.plan, scale, state.engineers[1])).toEqual(rows[1]);
    expect(timelineRow(state, state.plan, scale, state.engineers[2])).toEqual(rows[2]);
  });

  it('clamps visits past midnight and keeps raw values in the label', () => {
    const route = state.plan.routes[0];
    const plan = {
      ...state.plan,
      solver: 'dispatchers' as const,
      routes: [{ ...route, visits: [{ ...route.visits[0], start: '25:53', end: '49:13' }] }, ...state.plan.routes.slice(1)],
    };
    const scale = timeScale(state, plan);
    const bar = timelineRows(state, plan, scale)[0].bars[0];
    expect(bar).toMatchObject({ clipped: true, label: '25:53–49:13' });
    expect(bar.left + bar.width).toBeLessThanOrEqual(100);
  });

  it('keeps the request window for the bar title and «как можно скорее» with the start of waiting', () => {
    const asapState = makeAsapState();
    const scale = timeScale(asapState, asapState.plan);
    const rows = timelineRows(asapState, asapState.plan, scale);
    expect(rows[0].bars[2].window).toBe('окно 14:00–16:00');
    expect(rows[1].bars.map((bar) => [bar.requestId, bar.window])).toEqual([
      ['84627', 'окно 12:00–14:00'],
      ['URG-001', 'окно 13:00–15:00'],
      ['URG-002', 'как можно скорее с 13:00'],
    ]);
  });

  it('draws the lunch of a route as a bar at its time and nothing for a route without a lunch', () => {
    const plan = state.previous_plan!;
    const scale = timeScale(state, plan);
    const rows = timelineRows(state, plan, scale);
    expect(rows[0].lunch).toEqual({
      left: percent(scale, 810),
      width: percent(scale, 855) - percent(scale, 810),
      label: '13:30–14:15',
    });
    expect(rows[1].lunch?.label).toBe('13:05–13:50');
    expect(rows[2].lunch).toBeNull();
    const control = state.control!;
    expect(timelineRows(state, control, timeScale(state, control)).map((row) => row.lunch)).toEqual([null, null, null]);
  });

  it('extends the axis to fit a lunch', () => {
    const [e01, ...rest] = state.plan.routes;
    const plan = { ...state.plan, routes: [{ ...e01, lunch: { start: '23:00', end: '23:45' } }, ...rest] };
    expect(timeScale(state, plan).to).toBe(1440);
  });

  it('builds the day scale of the slider like the plan scale when the events fit inside it', () => {
    expect(dayScale(state, state.plan)).toEqual({ from: 540, to: 1380 });
    expect(dayScale(state, state.previous_plan!)).toEqual({ from: 540, to: 1380 });
  });

  it('keeps the day scale when the dispatcher switches to the plan before the event', () => {
    const [e01, ...rest] = state.previous_plan!.routes;
    const previous = { ...state.previous_plan!, routes: [{ ...e01, lunch: { start: '23:00', end: '23:45' } }, ...rest] };
    const withLate = { ...state, previous_plan: previous };
    expect(dayScale(withLate, withLate.plan)).toEqual({ from: 540, to: 1440 });
    expect(dayScale(withLate, previous)).toEqual({ from: 540, to: 1440 });
  });

  it('widens the day scale to whole hours around the events of the timeline', () => {
    const early = makeTimelineItem({ event: { type: 'cancel', time: '06:15', request: null, request_id: '50104', engineer_id: null } });
    const late = makeTimelineItem({ id: 'tl_2', event: { type: 'cancel', time: '23:20', request: null, request_id: '46393', engineer_id: null } });
    expect(dayScale({ ...state, timeline: [early] }, state.plan)).toEqual({ from: 360, to: 1380 });
    expect(dayScale({ ...state, timeline: [early, late] }, state.plan)).toEqual({ from: 360, to: 1440 });
  });

  it('produces hour ticks', () => {
    expect(hourTicks({ from: 540, to: 720 })).toEqual([540, 600, 660, 720]);
  });
});
