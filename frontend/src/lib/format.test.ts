import { describe, expect, it } from 'vitest';
import {
  addMinutes,
  formatDuration,
  formatKm,
  formatSigned,
  fromMinutes,
  isValidTime,
  laterTime,
  shortAddress,
  toMinutes,
} from './format';

describe('format', () => {
  it('converts HH:MM to minutes and back', () => {
    expect(toMinutes('09:30')).toBe(570);
    expect(toMinutes('0:01')).toBe(1);
    expect(fromMinutes(570)).toBe('09:30');
    expect(fromMinutes(1470)).toBe('24:30');
    expect(toMinutes('49:13')).toBe(2953);
    expect(toMinutes('125:00')).toBe(7500);
  });

  it('validates time strings', () => {
    expect(isValidTime('13:00')).toBe(true);
    expect(isValidTime('13:7')).toBe(false);
    expect(isValidTime('25:53')).toBe(true);
    expect(isValidTime('10:60')).toBe(false);
    expect(isValidTime('')).toBe(false);
    expect(() => toMinutes('abc')).toThrow('Некорректное время');
  });

  it('picks the later time and caps additions at the end of day', () => {
    expect(laterTime('12:00', '13:00')).toBe('13:00');
    expect(laterTime('14:00', '13:00')).toBe('14:00');
    expect(addMinutes('13:00', 120)).toBe('15:00');
    expect(addMinutes('23:00', 120)).toBe('23:59');
  });

  it('formats km and signed deltas in Russian style', () => {
    expect(formatKm(34.94)).toBe('34,9 км');
    expect(formatSigned(-2)).toBe('−2');
    expect(formatSigned(3.25, 1)).toBe('+3,3');
    expect(formatSigned(-0.01, 1)).toBe('0');
  });

  it('formats durations in hours and minutes', () => {
    expect(formatDuration(0)).toBe('0 мин');
    expect(formatDuration(45)).toBe('45 мин');
    expect(formatDuration(60)).toBe('1 ч');
    expect(formatDuration(365)).toBe('6 ч 5 мин');
  });

  it('shortens Moscow addresses and drops the flat', () => {
    expect(shortAddress('Город Москва, ул.Юности, д. 32, кв. 5')).toBe('ул.Юности, д. 32');
    expect(shortAddress('г.Город Москва, наб.Семеновская, д. 3/1к2')).toBe('наб.Семеновская, д. 3/1к2');
    expect(shortAddress('Кашира, ул.Победы, д. 9')).toBe('Кашира, ул.Победы, д. 9');
  });
});
