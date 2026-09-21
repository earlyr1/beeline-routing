import type { HHMM, Plan, PlanningState, ServiceRequest } from '../api/types';
import { requestLabel, shortAddress, toMinutes } from './format';
import { assignmentIndex } from './planView';

/**
 * Что клиенту в последний раз сказали про заявку: время визита и бригада.
 * start null — визита он не ждёт: либо ему сказали, что сегодня не приедем, либо не говорили ничего.
 */
export interface Promised {
  start: HHMM | null;
  engineer_id: string | null;
  /** Номер плана, на котором договорились. Отметки, записанные до появления номера, его не знают. */
  version?: number;
}

/** Согласованные времена по номеру заявки: их помнит стор и localStorage набора данных. */
export type AgreedTimes = Record<string, Promised>;

/** Красный — клиент остаётся без визита или визит вне его окна; жёлтый — сдвиг на час и больше; серый — остальное. */
export type CallSeverity = 'red' | 'yellow' | 'grey';

/**
 * Что случилось с заявкой с точки зрения клиента: визит переехал, пропал совсем или появился у заявки,
 * про которую клиенту ничего не обещали.
 */
export type CallKind = 'moved' | 'lost' | 'added';

/** Строка списка звонков: одна заявка, по которой план разошёлся с тем, что знает клиент. */
export interface CallRow {
  requestId: string;
  /** Номер для диспетчера: у срочной заявки с приставкой URG-. */
  label: string;
  address: string;
  kind: CallKind;
  severity: CallSeverity;
  /** Время, которое знает клиент; null — визита он не ждёт. */
  was: HHMM | null;
  /** Время в плане; null — сегодня к нему не приедут. */
  now: HHMM | null;
  /** Бригада в плане и бригада, которую знает клиент; null — без бригады. */
  engineerId: string | null;
  previousEngineerId: string | null;
}

/** Кому звонить и что уже согласовано. */
export interface CallList {
  pending: CallRow[];
  agreed: CallRow[];
}

const SEVERITY_ORDER: Record<CallSeverity, number> = { red: 0, yellow: 1, grey: 2 };

/** Сдвиг на час и больше клиенту нужно сообщить обязательно: он планировал день под прежнее время. */
export const BIG_SHIFT_MIN = 60;

/** Время и бригада заявки в плане номер version; null и null — заявки в маршрутах нет. */
export function plannedPromise(plan: Plan, requestId: string, version: number): Promised {
  const assigned = assignmentIndex(plan).get(requestId);
  return { start: assigned?.visit.start ?? null, engineer_id: assigned?.engineerId ?? null, version };
}

function kindOf(known: Promised, now: Promised): CallKind {
  if (now.start === null) return 'lost';
  if (known.start === null) return 'added';
  return 'moved';
}

function severityOf(
  kind: CallKind,
  known: Promised,
  now: Promised,
  lateMin: number,
  promised: boolean,
): CallSeverity {
  // Визит вне окна клиента — то же самое, что без визита: обещание не выполняется, и звонить нужно так же срочно.
  if (kind === 'lost' || lateMin > 0) return 'red';
  // Время клиенту уже называли, и план его не сдержал: такую строку нельзя терять среди мелких сдвигов,
  // даже если бригада приедет всего на полчаса раньше — клиента в это время может не быть дома.
  if (promised) return 'yellow';
  if (kind === 'added' || known.start === null || now.start === null) return 'grey';
  return Math.abs(toMinutes(now.start) - toMinutes(known.start)) >= BIG_SHIFT_MIN ? 'yellow' : 'grey';
}

function callRow(
  request: ServiceRequest,
  known: Promised,
  now: Promised,
  lateMin: number,
  promised: boolean,
): CallRow {
  const kind = kindOf(known, now);
  return {
    requestId: request.id,
    label: requestLabel(request.id, request.priority),
    address: shortAddress(request.address),
    kind,
    severity: severityOf(kind, known, now, lateMin, promised),
    was: known.start,
    now: now.start,
    engineerId: now.engineer_id,
    previousEngineerId: known.engineer_id,
  };
}

/** Сначала по важности, потом по времени визита: диспетчер звонит в том порядке, в каком идёт день. */
function byUrgency(a: CallRow, b: CallRow): number {
  const minutes = (row: CallRow) => (row.now ?? row.was ?? '23:59');
  return (
    SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] ||
    toMinutes(minutes(a)) - toMinutes(minutes(b)) ||
    a.requestId.localeCompare(b.requestId)
  );
}

/** События шкалы, до которых часы ещё не дошли: после них список звонков будет другим. */
export function eventsAhead(state: PlanningState): boolean {
  return (state.timeline ?? []).some((item) => item.status === 'pending' || item.status === 'awaiting');
}

/**
 * Кому звонить: разница между текущим планом и тем, что клиент знает.
 *
 * Клиенту ещё ничего не говорили — сравниваем с утренним планом; диспетчер отметил «Согласовано» — с тем временем,
 * о котором договорились. Это не разница последнего события: часы дня ходят вперёд и назад, и список по событию
 * врал бы. Зато так возвращается строка, которую уже согласовали на 16:20, если следующее событие увезло визит
 * обратно на 14:00: клиент ждёт в 16:20.
 *
 * Отменённые заявки в список не попадают: клиент либо сам отказался, либо ему уже сказали. Не попадают и визиты,
 * которые к времени на часах (clock) уже закончились: работа сделана, звонить не о чем. Договорённость, записанную
 * на плане новее текущего (часы отмотали назад), пропускаем целиком: сравнение с прежним планом перевернуло бы
 * строку и позвало отзывать время, которое никуда не делось.
 */
export function callList(state: PlanningState, agreed: AgreedTimes, clock: HHMM): CallList {
  const assigned = assignmentIndex(state.plan);
  const morning = new Map((state.morning ?? []).map((item) => [item.request_id, item]));
  const now = toMinutes(clock);
  const pending: CallRow[] = [];
  const settled: CallRow[] = [];
  for (const request of state.requests) {
    if (request.status === 'cancelled') continue;
    const visit = assigned.get(request.id);
    // Визит закончился до времени на часах: работа сделана, звонить поздно и незачем.
    if (visit && toMinutes(visit.visit.end) <= now) continue;
    const promise = agreed[request.id];
    // Договорились на плане новее текущего: сравнивать их нельзя, строка вышла бы перевёрнутой.
    if (promise && (promise.version ?? 0) > state.version) continue;
    const planned: Promised = { start: visit?.visit.start ?? null, engineer_id: visit?.engineerId ?? null };
    const morningVisit = morning.get(request.id);
    const known: Promised =
      promise ?? { start: morningVisit?.start ?? null, engineer_id: morningVisit?.engineer_id ?? null };
    const same = known.start === planned.start && known.engineer_id === planned.engineer_id;
    // Ничего не изменилось и звонка не было: сообщать нечего.
    if (same && promise === undefined) continue;
    const row = callRow(request, known, planned, visit?.visit.late_min ?? 0, promise !== undefined);
    (same ? settled : pending).push(row);
  }
  return { pending: pending.sort(byUrgency), agreed: settled.sort(byUrgency) };
}

/** Что уже сказали клиенту: строка нижнего блока вкладки. */
export function callAgreedText(row: CallRow): string {
  return row.now === null ? 'сказали, что сегодня не приедем' : `договорились на ${row.now}`;
}

/** Что сказать клиенту про время: «было 14:00 → стало 16:20», а у крайних случаев — словами. */
export function callChangeText(row: CallRow): string {
  if (row.kind === 'lost') return row.was === null ? 'сегодня не приедем' : `было ${row.was} → сегодня не приедем`;
  if (row.kind === 'added') return `приедем в ${row.now}`;
  return row.was === row.now ? `время прежнее, ${row.now}` : `было ${row.was} → стало ${row.now}`;
}
