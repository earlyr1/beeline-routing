import { describe, expect, it } from 'vitest';
import {
  DEFAULT_LUNCH_ENABLED,
  DEFAULT_WORKLOAD_LEVEL,
  WORKLOAD_LEVELS,
  clampWorkloadLevel,
  dayModeText,
  lunchEnabledOf,
  travelBufferText,
  workloadLevel,
} from './workload';

describe('workload levels', () => {
  it('mirrors the backend scale of three steps from a calm day to a day at the limit', () => {
    expect(DEFAULT_WORKLOAD_LEVEL).toBe(1);
    expect(WORKLOAD_LEVELS.map(({ level, emoji, title, hint }) => ({ level, emoji, title, hint }))).toEqual([
      { level: 0, emoji: '😌', title: 'Спокойный день', hint: 'Больше инженеров, больше запас на дорогу' },
      { level: 1, emoji: '😐', title: 'Обычный день', hint: 'Баланс между числом инженеров и пробегом' },
      { level: 2, emoji: '🥵', title: 'На пределе', hint: 'Меньше инженеров, каждому больше заявок' },
    ]);
    expect(WORKLOAD_LEVELS.map((item) => item.travelFactor)).toEqual([1.3, 1.1, 1]);
  });

  it('keeps any value inside the scale', () => {
    expect(clampWorkloadLevel(-1)).toBe(0);
    expect(clampWorkloadLevel(9)).toBe(2);
    expect(clampWorkloadLevel(0.6)).toBe(1);
    expect(clampWorkloadLevel(Number.NaN)).toBe(DEFAULT_WORKLOAD_LEVEL);
    expect(workloadLevel(7).title).toBe('На пределе');
  });

  it('describes the travel buffer of the level in percent', () => {
    expect(WORKLOAD_LEVELS.map((item) => travelBufferText(item.level))).toEqual([
      'Запас на дорогу: +30%',
      'Запас на дорогу: +10%',
      'Запас на дорогу: без запаса',
    ]);
  });
});

describe('lunch by plan', () => {
  it('is on by default, also for a session of an older backend without the flag', () => {
    expect(DEFAULT_LUNCH_ENABLED).toBe(true);
    expect(lunchEnabledOf(undefined)).toBe(true);
    expect(lunchEnabledOf(true)).toBe(true);
    expect(lunchEnabledOf(false)).toBe(false);
  });

  it('names the mode of the day: the workload level and the lunch', () => {
    expect(dayModeText(1, true)).toBe('😐 Обычный день · с обедом');
    expect(dayModeText(2, false)).toBe('🥵 На пределе · без обеда');
    expect(dayModeText(0, false)).toBe('😌 Спокойный день · без обеда');
  });
});
