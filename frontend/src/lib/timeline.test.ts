import { describe, expect, it } from 'vitest';
import { makePlanningState } from '../test/fixtures';
import { hourTicks, percent, timelineRows, timeScale } from './timeline';

describe('timeline', () => {
  const state = makePlanningState();

  it('uses the default 08:00–23:00 axis when data fits inside it', () => {
    expect(timeScale(state, state.plan)).toEqual({ from: 480, to: 1380 });
  });

  it('extends the axis to fit data but never beyond 00:00–24:00', () => {
    const early = { ...state, engineers: state.engineers.map((engineer) => ({ ...engineer, shift_start: '06:30' })) };
    expect(timeScale(early, state.plan).from).toBe(360);

    const route = state.plan.routes[0];
    const overnight = {
      ...state.plan,
      routes: [{ ...route, visits: [{ ...route.visits[0], start: '23:10', end: '49:13' }] }, ...state.plan.routes.slice(1)],
    };
    expect(timeScale(state, overnight)).toEqual({ from: 480, to: 1440 });
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

  it('produces hour ticks', () => {
    expect(hourTicks({ from: 540, to: 720 })).toEqual([540, 600, 660, 720]);
  });
});
