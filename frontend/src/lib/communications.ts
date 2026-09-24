import type { AgreedCall, AgreedCalls, HHMM, PlanEvent, PlanningState, ServiceRequest, TimeSlot, TimeWindow } from '../api/types';
import { fromMinutes, requestLabel, requestWindowPhrase, requestWindowText, shortAddress, toMinutes } from './format';
import { assignmentIndex, byId } from './planView';
import { offGrid, slotFor } from './windows';

// Окно клиента (TimeWindow) и договорённость (AgreedCall, AgreedCalls) описаны в контракте API: отметка звонка —
// событие «Коммуникация» на шкале дня, и вкладка получает договорённости в составе плана.
export type { AgreedCall, AgreedCalls, TimeWindow };

/**
 * Отметка звонка на экране: договорённость с сервера или та, что ещё в пути. У отметки в пути entry_id null:
 * события на шкале пока нет, и снять её нечем. Строка уходит вниз сразу — диспетчер уже положил трубку.
 */
export interface AgreedMark {
  window: TimeWindow | null;
  entry_id: string | null;
}

export type AgreedMarks = Record<string, AgreedMark>;

/** Красный — обещание не выполняется; жёлтый — окно стало другим, и клиент должен узнать новое. */
export type CallSeverity = 'red' | 'yellow';

/**
 * Почему звоним: сегодня к клиенту не приедем, не попадаем в его окно или у заявки теперь другое окно.
 * Ни смена бригады, ни переезд визита внутри окна причиной не бывают: клиенту обещали окно, а не минуту.
 */
export type CallKind = 'lost' | 'outside' | 'window';

/** Строка списка звонков: одна заявка, по которой план разошёлся с тем, что знает клиент. */
export interface CallRow {
  requestId: string;
  /** Номер для диспетчера: у срочной заявки с приставкой URG-. */
  label: string;
  address: string;
  kind: CallKind;
  severity: CallSeverity;
  /** Окно, которое знает клиент; null — ему сказали, что сегодня не приедем. */
  known: TimeWindow | null;
  /** Окно, в которое план не попадает: его и называть нельзя. null — у остальных поводов для звонка. */
  missed: TimeWindow | null;
  /** Окно, которое диспетчер назовёт клиенту: слот сетки, если окно новое. null — сегодня к нему не приедут. */
  promise: TimeWindow | null;
  /** Окно называть поздно: визит уже идёт, а слот вокруг него к времени на часах закончился. */
  underway: boolean;
  /** Начало визита в плане; null — сегодня к клиенту не приедут. Клиенту эту минуту не называют. */
  start: HHMM | null;
  /** Бригада плана: справка для диспетчера, а не причина звонка. */
  engineerId: string | null;
}

/** Строка нижнего блока: с этим клиентом уже договорились, звонить не о чем. */
export interface AgreedRow {
  requestId: string;
  label: string;
  address: string;
  /** Окно, о котором договорились; null — клиенту сказали, что сегодня не приедем. */
  known: TimeWindow | null;
  /** Событие «Коммуникация» на шкале: его удаление снимает отметку. null — отметка ещё в пути к серверу. */
  entryId: string | null;
}

/** Кому звонить и с кем уже договорились. */
export interface CallList {
  pending: CallRow[];
  agreed: AgreedRow[];
}

const windowOf = (item: { window_start: HHMM; window_end: HHMM; asap?: boolean }): TimeWindow => ({
  start: item.window_start,
  end: item.window_end,
  asap: item.asap ?? false,
});

const sameWindow = (a: TimeWindow, b: TimeWindow) => a.start === b.start && a.end === b.end;

/** Визит попадает в окно: начало работы не раньше начала окна и не позже его конца. */
const fits = (window: TimeWindow, start: HHMM) =>
  toMinutes(window.start) <= toMinutes(start) && toMinutes(start) <= toMinutes(window.end);

/** Без сетки окон (старый сервер, офлайн) новое окно длится столько же, сколько окно заявки: от часа до четырёх. */
const SHORTEST_WINDOW = 60;
const LONGEST_WINDOW = 240;
/** Без сетки новое окно начинается с ровной получасовой отметки: клиенту называют окно, а не минуту плана. */
const WINDOW_STEP = 30;

/**
 * Окно, которое диспетчер назовёт вместо сорванного: слот сетки, в который попадает визит, а если визит вышел
 * за рабочий день — ближайший слот, других окон у сетки нет. Клиенту называют слот, а не произвольный интервал.
 *
 * Без сетки от сервера окно прежнее: получасовая отметка перед визитом и столько же времени, сколько было
 * в окне заявки. Ровная отметка держит окно на месте, пока визит ходит по плану туда-сюда.
 */
function suggestedWindow(window: TimeWindow, start: HHMM, grid: TimeSlot[]): TimeWindow {
  const slot = slotFor(grid, start);
  if (slot) return { start: slot.start, end: slot.end, asap: false };
  const length = Math.min(LONGEST_WINDOW, Math.max(SHORTEST_WINDOW, toMinutes(window.end) - toMinutes(window.start)));
  const from = Math.floor(toMinutes(start) / WINDOW_STEP) * WINDOW_STEP;
  return { start: fromMinutes(from), end: fromMinutes(from + length), asap: false };
}

/**
 * Окно, которое диспетчер назовёт клиенту: своё окно заявки, а если визит в него не попадает — слот вокруг визита.
 * Окно, которого в сетке нет, тоже называют слотом: у аварии выгрузки там весь день, 00:01–23:59, и это пометка
 * данных, а не обещание клиенту. Окно «как можно скорее» остаётся как есть: его называют словами.
 */
function promisedWindow(window: TimeWindow, start: HHMM | null, grid: TimeSlot[]): TimeWindow | null {
  if (start === null) return null;
  const named = window.asap === true || !offGrid(grid, window.start, window.end);
  return named && fits(window, start) ? window : suggestedWindow(window, start, grid);
}

/**
 * Окно называть поздно: визит уже идёт, а слот вокруг его начала к времени на часах закончился.
 * Слот берётся по началу визита, а оно в прошлом, поэтому к моменту звонка такой слот может уже кончиться:
 * клиенту в этом случае говорят не про окно, а что бригада у него.
 */
function isUnderway(promise: TimeWindow | null, start: HHMM | null, clock: HHMM): boolean {
  if (promise === null || start === null) return false;
  const now = toMinutes(clock);
  return toMinutes(start) <= now && toMinutes(promise.end) <= now;
}

/**
 * Окно, которое клиент узнаёт из разговора: своё окно заявки, если план в него попадает, иначе слот вокруг визита.
 * null — визита сегодня нет: клиенту говорят, что сегодня не приедем, и заявка переносится.
 */
export function agreedWindow(state: PlanningState, requestId: string, grid: TimeSlot[] = []): TimeWindow | null {
  const request = byId(state.requests).get(requestId);
  if (!request) return null;
  const visit = assignmentIndex(state.plan).get(requestId);
  return promisedWindow(windowOf(request), visit?.visit.start ?? null, grid);
}

/**
 * Событие «Коммуникация» для шкалы: что клиенту сказали, во время на часах. Названное окно сервер делает окном
 * заявки, «сегодня не приедем» (null) — переносит заявку.
 */
export function agreementEvent(requestId: string, window: TimeWindow | null, time: HHMM): PlanEvent {
  return { type: 'client_agreed', time, request: null, request_id: requestId, engineer_id: null, agreed_window: window };
}

/** Повод для звонка: какое окно план не выполняет и какое окно диспетчер назовёт вместо него. */
interface CallReason {
  kind: CallKind;
  missed: TimeWindow | null;
  promise: TimeWindow | null;
}

/**
 * Почему звоним клиенту, или null — звонить не о чем.
 *
 * Клиенту называют окно, а не минуту: визит, переехавший внутри окна, и другая бригада — не повод для звонка.
 * Повод ровно три: сегодня не приедем, визит вне окна клиента и новое окно самой заявки. Окно, которое диспетчер
 * назовёт, — то, которое план выполняет: своё окно заявки, если визит в него попадает, иначе новое вокруг визита.
 */
function callReason(
  known: TimeWindow | null,
  base: TimeWindow,
  window: TimeWindow,
  start: HHMM | null,
  grid: TimeSlot[],
): CallReason | null {
  // Визита сегодня нет: звоним, если клиент ещё ждёт бригаду.
  if (start === null) return known === null ? null : { kind: 'lost', missed: null, promise: null };
  // Клиенту сказали, что сегодня не приедем, а визит вернулся: он должен узнать окно.
  if (known === null) return { kind: 'window', missed: null, promise: promisedWindow(window, start, grid) };
  // Диспетчер передвинул окно заявки уже после того, как клиент узнал своё: назовём новое.
  const moved = !sameWindow(base, window);
  const named = moved ? window : known;
  // В окно, которое собирались назвать, план не попадает: клиенту нужно другое.
  if (!fits(named, start)) return { kind: 'outside', missed: named, promise: suggestedWindow(window, start, grid) };
  // Окно, которое диспетчер поставил сам, называют как есть: сетку ему уже не обойти, и подменять его нечем.
  return moved ? { kind: 'window', missed: null, promise: window } : null;
}

/** Сначала срыв обещания, потом перенос окна, внутри — по времени дня: диспетчер звонит по ходу дня. */
function byUrgency(a: CallRow, b: CallRow): number {
  const when = (row: CallRow) => row.start ?? row.known?.start ?? row.promise?.start ?? '23:59';
  return (
    (a.severity === b.severity ? 0 : a.severity === 'red' ? -1 : 1) ||
    toMinutes(when(a)) - toMinutes(when(b)) ||
    a.requestId.localeCompare(b.requestId)
  );
}

function byWindow(a: AgreedRow, b: AgreedRow): number {
  return (
    toMinutes(a.known?.start ?? '23:59') - toMinutes(b.known?.start ?? '23:59') ||
    a.requestId.localeCompare(b.requestId)
  );
}

/** События шкалы, до которых часы ещё не дошли: после них список звонков будет другим. */
export function eventsAhead(state: PlanningState): boolean {
  return (state.timeline ?? []).some((item) => item.status === 'pending' || item.status === 'awaiting');
}

/**
 * Окно, с которым заявка вошла в день позже утра: её приняли среди дня, и клиенту назвали окно тогда.
 * У срочной заявки оно лежит в событии `urgent`, у остальных — в `previous_request` первой правки.
 */
function acceptedWindows(state: PlanningState): Map<string, TimeWindow> {
  const accepted = new Map<string, TimeWindow>();
  const requestOf = (event: PlanEvent): ServiceRequest | null => {
    if (event.type === 'urgent') return event.request;
    return event.type === 'request_updated' ? (event.previous_request ?? null) : null;
  };
  for (const applied of state.events ?? []) {
    // Заявку могли править не раз: окно приёма несёт первое событие по ней.
    const request = requestOf(applied.event);
    if (request && !accepted.has(request.id)) accepted.set(request.id, windowOf(request));
  }
  return accepted;
}

/**
 * Кому звонить: где план не выполняет того, что клиенту обещали.
 *
 * Клиент знает не время визита, а окно: то, которое ему назвали при отметке «Согласовано», иначе то, с которым
 * заявка вошла в день (правка окна — такое же событие шкалы, поэтому утреннее окно не меняется вместе с ней).
 * У заявки, принятой среди дня, окно приёма лежит в событии, которым она в день вошла.
 * Сравнивается не последнее событие, а обещание клиенту: часы дня ходят вперёд и назад, и список по событию врал бы.
 * Отметки — события шкалы, применённые к времени плана: часы, отведённые раньше звонка, снимают его и на сервере.
 *
 * Отменённые заявки в список не попадают: клиент либо сам отказался, либо ему уже сказали. Не попадают и визиты,
 * которые к времени на часах (clock) уже закончились: работа сделана, звонить не о чем. Перенесённая заявка
 * («сегодня не приедем») стоит в блоке «Согласовано»: ей уже сказали, и звонка она не ждёт.
 */
export function callList(state: PlanningState, agreed: AgreedMarks, clock: HHMM, grid: TimeSlot[] = []): CallList {
  const assigned = assignmentIndex(state.plan);
  const morning = new Map((state.morning ?? []).map((item) => [item.request_id, windowOf(item)]));
  const accepted = acceptedWindows(state);
  const now = toMinutes(clock);
  const pending: CallRow[] = [];
  const settled: AgreedRow[] = [];
  for (const request of state.requests) {
    if (request.status === 'cancelled') continue;
    const visit = assigned.get(request.id);
    // Визит закончился до времени на часах: работа сделана, звонить поздно и незачем.
    if (visit && toMinutes(visit.visit.end) <= now) continue;
    const mark = agreed[request.id];
    const window = windowOf(request);
    // Окно, с которым заявка вошла в день: утреннее, у принятой среди дня — окно приёма.
    const entered = morning.get(request.id) ?? accepted.get(request.id) ?? window;
    // В отметке записано окно, которое клиенту назвали: null — ему сказали, что сегодня не приедем.
    const known = mark ? (mark.window ?? null) : entered;
    // Окно заявки сразу после разговора. Звонок на шкале сделал названное окно окном заявки, поэтому дальше
    // расхождения считаются от него: передвинул диспетчер окно после звонка — клиент должен узнать новое.
    // Отметка в пути окна заявки ещё не поменяла, и сравнивать её пока можно только с текущим окном.
    const base = mark ? (mark.entry_id === null ? window : (mark.window ?? window)) : entered;
    const start = visit?.visit.start ?? null;
    const reason = callReason(known, base, window, start, grid);
    const label = requestLabel(request.id, request.priority);
    const address = shortAddress(request.address);
    if (reason === null) {
      // Клиенту звонили, и с тех пор ничего не разошлось: строка уходит в блок «Согласовано».
      if (mark) settled.push({ requestId: request.id, label, address, known, entryId: mark.entry_id });
      continue;
    }
    pending.push({
      requestId: request.id,
      label,
      address,
      kind: reason.kind,
      severity: reason.kind === 'window' ? 'yellow' : 'red',
      known,
      missed: reason.missed,
      promise: reason.promise,
      underway: isUnderway(reason.promise, start, clock),
      start,
      engineerId: visit?.engineerId ?? null,
    });
  }
  return { pending: pending.sort(byUrgency), agreed: settled.sort(byWindow) };
}

const asRequest = (window: TimeWindow) => ({
  asap: window.asap ?? false,
  window_start: window.start,
  window_end: window.end,
});

/** Окно словами диспетчера: «12:00–14:00» или «как можно скорее с 13:00». */
export function windowText(window: TimeWindow): string {
  return requestWindowText(asRequest(window));
}

/** То же с подписью: «окно 12:00–14:00» или «как можно скорее с 13:00». */
function windowPhrase(window: TimeWindow): string {
  return requestWindowPhrase(asRequest(window));
}

/** Окно там, где по-русски нужен предлог: «в окно 12:00–14:00», «как можно скорее, с 13:00». */
function inWindow(window: TimeWindow): string {
  return window.asap ? `как можно скорее, с ${window.start}` : `в окно ${windowText(window)}`;
}

/** Окно заявки стало другим: «окно было 12:00–14:00 → стало 16:00–18:00». */
function changedText(from: TimeWindow, to: TimeWindow): string {
  if (from.asap || to.asap) return `${windowPhrase(from)} → ${windowPhrase(to)}`;
  return `окно было ${windowText(from)} → стало ${windowText(to)}`;
}

/** Что уже сказали клиенту: строка нижнего блока вкладки. */
export function callAgreedText(row: AgreedRow): string {
  if (row.known === null) return 'сказали, что сегодня не приедем';
  return row.known.asap ? `договорились: ${windowText(row.known)}` : `договорились на окно ${windowText(row.known)}`;
}

/** Что сказать клиенту: разговор идёт про окна, а не про минуты, поэтому минуты плана в строке нет. */
export function callChangeText(row: CallRow): string {
  if (row.promise === null) {
    return row.known === null ? 'сегодня не приедем' : `сегодня не приедем, обещали ${windowPhrase(row.known)}`;
  }
  // Слот вокруг визита уже кончился: называть его нечестно, и клиенту говорят, что бригада у него.
  const arrived = 'бригада уже у клиента';
  if (row.known === null) {
    return `сказали, что сегодня не приедем → ${row.underway ? arrived : `приедем ${inWindow(row.promise)}`}`;
  }
  if (row.missed !== null) {
    return `не попадаем ${inWindow(row.missed)} — ${row.underway ? arrived : `назовите ${windowPhrase(row.promise)}`}`;
  }
  return changedText(row.known, row.promise);
}
