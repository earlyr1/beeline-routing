/**
 * Сетка окон визита на стороне диспетчера: клиенту называют слот, а не произвольный интервал.
 *
 * Сами слоты приходят от сервера (GET /api/config, поле window_grid) и здесь не задаются: сервер строит их
 * от смены и длины окна, и разойтись с ним клиенту нечем. Так устроены и настоящие данные: у заявок трёх
 * реальных регионов окна ровно шесть двухчасовых, 10:00–12:00 … 20:00–22:00, а весь день стоит только у аварий.
 * Пустая сетка означает, что сервер её не прислал (старый сервер или офлайн): тогда окно вводится временем.
 */
import type { HHMM, TimeSlot } from '../api/types';
import { formatWindow, isValidTime, toMinutes } from './format';

/** Слот словами диспетчера: «12:00–14:00». */
export const slotText = (slot: TimeSlot): string => formatWindow(slot.start, slot.end);

/** Значение слота в списке выбора. */
export const slotKey = (slot: TimeSlot): string => `${slot.start}-${slot.end}`;

/** Слот сетки по значению списка выбора; null — такого слота в сетке нет. */
export function slotByKey(grid: TimeSlot[], key: string): TimeSlot | null {
  return grid.find((slot) => slotKey(slot) === key) ?? null;
}

/** Слот сетки с такими границами; null — окно не с сетки (например, окно заявки из данных). */
export function slotOf(grid: TimeSlot[], start: HHMM, end: HHMM): TimeSlot | null {
  return grid.find((slot) => slot.start === start && slot.end === end) ?? null;
}

/**
 * Слот, в который попадает время, иначе ближайший к нему; null — сетки нет.
 * Граница достаётся следующему слоту: 12:00 — начало 12:00–14:00, а не конец 10:00–12:00. Время вне рабочего дня
 * своего слота не имеет, и тогда называют ближайший: другого окна сетке предложить нечего.
 */
export function slotFor(grid: TimeSlot[], time: HHMM): TimeSlot | null {
  if (grid.length === 0) return null;
  const minute = toMinutes(time);
  const inside = grid.find((slot) => toMinutes(slot.start) <= minute && minute < toMinutes(slot.end));
  if (inside) return inside;
  const distance = (slot: TimeSlot) => Math.min(Math.abs(toMinutes(slot.start) - minute), Math.abs(toMinutes(slot.end) - minute));
  return grid.reduce((best, slot) => (distance(slot) < distance(best) ? slot : best));
}

/** Окно не с сетки, и называть его нельзя. Без сетки проверять нечего. */
export function offGrid(grid: TimeSlot[], start: HHMM, end: HHMM): boolean {
  return grid.length > 0 && slotOf(grid, start, end) === null;
}

/** Отказ в форме: такого окна клиенту не называют. */
export function offGridError(grid: TimeSlot[]): string {
  return `Выберите окно визита из сетки: ${grid.map(slotText).join(', ')}`;
}

/**
 * Окно к времени события уже закончилось. Такое окно сервер не принимает («окно заканчивается раньше времени
 * события»), поэтому к вечеру выбирать остаётся из оставшихся слотов. Время не разобрать — проверять нечего.
 */
export function windowPassed(end: HHMM, time: HHMM): boolean {
  return isValidTime(time) && isValidTime(end) && toMinutes(end) <= toMinutes(time);
}

/** Отказ в форме: этот слот уже прошёл. */
export function passedWindowError(start: HHMM, end: HHMM): string {
  return `Окно ${formatWindow(start, end)} уже закончилось: выберите слот, который ещё не прошёл`;
}

/** Слоты, которые к времени события ещё не закончились: только их сервер и примет. Время не разобрать — вся сетка. */
export function openSlots(grid: TimeSlot[], time: HHMM): TimeSlot[] {
  return isValidTime(time) ? grid.filter((slot) => !windowPassed(slot.end, time)) : grid;
}

/** Слот в списке выбора: идущий и прошедший подписаны, чтобы диспетчер видел, где он во времени дня. */
export function slotOption(slot: TimeSlot, time: HHMM): string {
  if (!isValidTime(time)) return slotText(slot);
  if (windowPassed(slot.end, time)) return `${slotText(slot)} (уже прошло)`;
  return toMinutes(slot.start) <= toMinutes(time) ? `${slotText(slot)} (идёт сейчас)` : slotText(slot);
}

/**
 * Слот, который подставляют по умолчанию: первый, в который ещё успеваем — до его конца остаётся не меньше, чем
 * нужно на работы (у идущего слота считается остаток). Слот, который вот-вот кончится, клиенту не называют:
 * бригаде в него не приехать, и заявка осталась бы без инженера. Не успеваем никуда (конец дня) — последний слот.
 */
export function defaultSlot(grid: TimeSlot[], time: HHMM, durationMin: number): TimeSlot | null {
  if (grid.length === 0 || !isValidTime(time)) return null;
  const minute = toMinutes(time);
  const needed = Number.isFinite(durationMin) && durationMin > 0 ? durationMin : 1;
  const fits = (slot: TimeSlot) => {
    const length = toMinutes(slot.end) - toMinutes(slot.start);
    return toMinutes(slot.end) - Math.max(minute, toMinutes(slot.start)) >= Math.min(needed, length);
  };
  return grid.find(fits) ?? grid[grid.length - 1];
}
