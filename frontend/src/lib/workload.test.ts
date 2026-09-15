import { describe, expect, it } from 'vitest';
import { DEFAULT_WORKLOAD_LEVEL, WORKLOAD_LEVELS, clampWorkloadLevel, travelBufferText, workloadLevel } from './workload';

describe('workload levels', () => {
  it('mirrors the backend scale from a calm day to a day at the limit', () => {
    expect(DEFAULT_WORKLOAD_LEVEL).toBe(2);
    expect(WORKLOAD_LEVELS.map(({ level, emoji, title, hint }) => ({ level, emoji, title, hint }))).toEqual([
      { level: 0, emoji: '😌', title: 'Спокойный день', hint: 'Больше инженеров, у каждого свободнее день' },
      { level: 1, emoji: '🙂', title: 'Без спешки', hint: 'Больше инженеров, у каждого свободнее день' },
      { level: 2, emoji: '😐', title: 'Обычный день', hint: 'Баланс между числом инженеров и пробегом' },
      { level: 3, emoji: '😓', title: 'Плотный день', hint: 'Меньше инженеров, каждому больше заявок' },
      { level: 4, emoji: '🥵', title: 'На пределе', hint: 'Меньше инженеров, каждому больше заявок' },
    ]);
    expect(WORKLOAD_LEVELS.map((item) => item.travelFactor)).toEqual([1.3, 1.2, 1.1, 1.05, 1]);
  });

  it('keeps any value inside the scale', () => {
    expect(clampWorkloadLevel(-1)).toBe(0);
    expect(clampWorkloadLevel(9)).toBe(4);
    expect(clampWorkloadLevel(2.6)).toBe(3);
    expect(clampWorkloadLevel(Number.NaN)).toBe(DEFAULT_WORKLOAD_LEVEL);
    expect(workloadLevel(7).title).toBe('На пределе');
  });

  it('describes the travel buffer of the level in percent', () => {
    expect(WORKLOAD_LEVELS.map((item) => travelBufferText(item.level))).toEqual([
      'Запас на дорогу: +30%',
      'Запас на дорогу: +20%',
      'Запас на дорогу: +10%',
      'Запас на дорогу: +5%',
      'Запас на дорогу: без запаса',
    ]);
  });
});
