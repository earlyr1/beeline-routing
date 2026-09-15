import { describe, expect, it } from 'vitest';
import { makePlanningState } from '../test/fixtures';
import { kmPerEngineerRows, metricRows } from './comparison';

describe('comparison', () => {
  const state = makePlanningState();

  it('compares the optimized plan with baseline and dispatchers', () => {
    const rows = metricRows(state);
    expect(rows.find((row) => row.label === 'Задействовано инженеров')).toMatchObject({
      baseline: '2',
      optimized: '2',
      dispatchers: '3',
      delta: '0',
      verdict: 'same',
    });
    expect(rows.find((row) => row.label === 'Не назначено')).toMatchObject({
      baseline: '2',
      optimized: '1',
      delta: '−1',
      verdict: 'better',
    });
    expect(rows.find((row) => row.label === 'Суммарный пробег')).toMatchObject({
      baseline: '32,2 км',
      optimized: '34,9 км',
      delta: '+2,7 км',
      verdict: 'worse',
    });
    expect(rows.find((row) => row.label === 'Нарушений ограничений')?.dispatchers).toBe('2');
  });

  it('shows missing dispatcher data', () => {
    expect(metricRows({ ...state, control: null })[0].dispatchers).toBe('нет данных');
  });

  it('lists km per engineer across all plans', () => {
    const rows = kmPerEngineerRows(state);
    expect(rows.map((row) => row.engineerId)).toEqual(['E01', 'E02', 'E03']);
    expect(rows[2]).toMatchObject({ baseline: null, optimized: null, dispatchers: 9.8 });
  });
});
