import { describe, expect, it } from 'vitest';
import { WINDOW_GRID } from '../test/fixtures';
import {
  defaultSlot,
  offGrid,
  offGridError,
  openSlots,
  passedWindowError,
  slotByKey,
  slotFor,
  slotKey,
  slotOf,
  slotOption,
  slotText,
  windowPassed,
} from './windows';

describe('сетка окон визита', () => {
  it('говорит слотами, а не минутами', () => {
    expect(WINDOW_GRID.map(slotText)).toEqual([
      '10:00–12:00',
      '12:00–14:00',
      '14:00–16:00',
      '16:00–18:00',
      '18:00–20:00',
      '20:00–22:00',
    ]);
    expect(slotKey(WINDOW_GRID[1])).toBe('12:00-14:00');
    expect(slotByKey(WINDOW_GRID, '12:00-14:00')).toEqual({ start: '12:00', end: '14:00' });
    expect(slotByKey(WINDOW_GRID, '11:30-13:30')).toBeNull();
    expect(slotOf(WINDOW_GRID, '14:00', '16:00')).toEqual({ start: '14:00', end: '16:00' });
    expect(slotOf(WINDOW_GRID, '15:00', '17:00')).toBeNull();
  });

  it('берёт слот визита, а на границе отдаёт следующий', () => {
    expect(slotFor(WINDOW_GRID, '10:00')).toEqual({ start: '10:00', end: '12:00' });
    expect(slotFor(WINDOW_GRID, '11:59')).toEqual({ start: '10:00', end: '12:00' });
    // 12:00 — начало 12:00–14:00, а не конец 10:00–12:00: клиенту называют окно, в которое визит попадает.
    expect(slotFor(WINDOW_GRID, '12:00')).toEqual({ start: '12:00', end: '14:00' });
    expect(slotFor(WINDOW_GRID, '15:10')).toEqual({ start: '14:00', end: '16:00' });
  });

  it('вне рабочего дня называет ближайший слот: других окон у сетки нет', () => {
    expect(slotFor(WINDOW_GRID, '09:00')).toEqual({ start: '10:00', end: '12:00' });
    expect(slotFor(WINDOW_GRID, '22:30')).toEqual({ start: '20:00', end: '22:00' });
  });

  it('без сетки от сервера не решает за диспетчера', () => {
    expect(slotFor([], '13:00')).toBeNull();
    expect(offGrid([], '11:30', '13:30')).toBe(false);
  });

  it('узнаёт окно не с сетки и говорит, из чего выбирать', () => {
    expect(offGrid(WINDOW_GRID, '11:30', '13:30')).toBe(true);
    expect(offGrid(WINDOW_GRID, '12:00', '14:00')).toBe(false);
    // Окно аварии из данных — весь день: слотом оно не бывает.
    expect(offGrid(WINDOW_GRID, '00:01', '23:59')).toBe(true);
    expect(offGridError(WINDOW_GRID)).toBe(
      'Выберите окно визита из сетки: 10:00–12:00, 12:00–14:00, 14:00–16:00, 16:00–18:00, 18:00–20:00, 20:00–22:00',
    );
  });

  it('предлагает только слоты, которые ещё не прошли, и подписывает идущий', () => {
    // В 13:30 первый слот уже закончился: такое окно сервер не примет, и выбирать его незачем.
    expect(openSlots(WINDOW_GRID, '13:30').map(slotText)).toEqual([
      '12:00–14:00',
      '14:00–16:00',
      '16:00–18:00',
      '18:00–20:00',
      '20:00–22:00',
    ]);
    // Граница: слот, кончающийся ровно в это время, уже прошёл.
    expect(openSlots(WINDOW_GRID, '12:00').map(slotText)).not.toContain('10:00–12:00');
    // Время ещё не введено — вся сетка: решать за диспетчера нечем.
    expect(openSlots(WINDOW_GRID, '').length).toBe(6);
    expect(openSlots(WINDOW_GRID, '22:30')).toEqual([]);

    expect(slotOption(WINDOW_GRID[1], '13:30')).toBe('12:00–14:00 (идёт сейчас)');
    expect(slotOption(WINDOW_GRID[0], '13:30')).toBe('10:00–12:00 (уже прошло)');
    expect(slotOption(WINDOW_GRID[2], '13:30')).toBe('14:00–16:00');
    expect(slotOption(WINDOW_GRID[2], '')).toBe('14:00–16:00');

    expect(windowPassed('12:00', '13:30')).toBe(true);
    expect(windowPassed('14:00', '13:30')).toBe(false);
    expect(windowPassed('14:00', '')).toBe(false);
    expect(passedWindowError('10:00', '12:00')).toBe(
      'Окно 10:00–12:00 уже закончилось: выберите слот, который ещё не прошёл',
    );
  });

  it('по умолчанию берёт слот, в который ещё успеваем с работами', () => {
    // До 14:00 остаётся 45 минут, а работы идут час: бригаде в этот слот не приехать, называют следующий.
    expect(defaultSlot(WINDOW_GRID, '13:15', 60)).toEqual({ start: '14:00', end: '16:00' });
    // Работ на 20 минут — успеваем и в идущий слот.
    expect(defaultSlot(WINDOW_GRID, '13:15', 20)).toEqual({ start: '12:00', end: '14:00' });
    expect(defaultSlot(WINDOW_GRID, '09:00', 80)).toEqual({ start: '10:00', end: '12:00' });
    // Работы длиннее слота: слот не удлинить, и берётся ближайший целый.
    expect(defaultSlot(WINDOW_GRID, '13:15', 240)).toEqual({ start: '14:00', end: '16:00' });
    // К концу дня успеть уже некуда: остаётся последний слот сетки, а не первый.
    expect(defaultSlot(WINDOW_GRID, '21:40', 60)).toEqual({ start: '20:00', end: '22:00' });
    expect(defaultSlot(WINDOW_GRID, '23:30', 60)).toEqual({ start: '20:00', end: '22:00' });
    // Длительность ещё не введена — слот выбирается по времени, а не отправляется в конец дня.
    expect(defaultSlot(WINDOW_GRID, '13:15', Number.NaN)).toEqual({ start: '12:00', end: '14:00' });
    expect(defaultSlot([], '13:15', 60)).toBeNull();
    expect(defaultSlot(WINDOW_GRID, '', 60)).toBeNull();
  });
});
