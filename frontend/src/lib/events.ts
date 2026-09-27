import type { DelayForecast, Engineer, HHMM, Plan, PlanEvent, Priority, ServiceRequest, Skill, TimeSlot, Transport, Visit, WorkType } from '../api/types';
import { requestClockStatus } from './clock';
import {
  addMinutes,
  formatWindow,
  isValidTime,
  laterTime,
  plural,
  PRIORITY_LABELS,
  requestLabel,
  requestLabelOf,
  requestWindowPhrase,
  shortAddress,
  SKILL_LABELS,
  toMinutes,
  TRANSPORT_LABELS,
} from './format';
import { defaultSlot, offGrid, offGridError, passedWindowError, windowPassed } from './windows';

/** Длина окна срочной заявки, когда сетки окон от сервера нет (старый сервер, офлайн), минут. */
export const URGENT_WINDOW_MIN = 120;

export interface PickedPoint {
  lat: number;
  lon: number;
}

export interface UrgentForm {
  /** Тип работ, по нормативам которого заполнена форма; null — сервер типов не прислал, навык выбирают вручную. */
  workType: WorkType | null;
  address: string;
  point: PickedPoint | null;
  windowStart: HHMM;
  windowEnd: HHMM;
  durationMin: number;
  skill: Skill;
  transport: Transport | '';
  time: HHMM;
  /** Как можно скорее: окно задаёт сервер, от времени события до конца смен; поля окна скрыты. */
  asap: boolean;
  /** Инженер везёт с собой единицу оборудования: роутер, приставку или колонку. */
  needsEquipment: boolean;
}

/** Поля срочной заявки, которые заполняет тип работ. */
export type WorkTypeFields = Pick<UrgentForm, 'workType' | 'skill' | 'durationMin' | 'transport' | 'needsEquipment' | 'asap'>;

/** Тип работ заполняет навык, длительность, транспорт, оборудование и «как можно скорее» нормативами сервера. */
export function workTypeFields(workType: WorkType): WorkTypeFields {
  return {
    workType,
    skill: workType.skill,
    durationMin: workType.duration_min,
    transport: workType.transport_required ?? '',
    needsEquipment: workType.needs_equipment,
    asap: workType.asap,
  };
}

/** Без типов работ от сервера (старый сервер или сервер недоступен) форма прежняя: аварийные работы на час на автомобиле. */
export const MANUAL_URGENT_FIELDS: WorkTypeFields = {
  workType: null,
  skill: 'emergency',
  durationMin: 60,
  transport: 'car',
  needsEquipment: false,
  asap: false,
};

/** Что заполнил тип работ, одной строкой, например «Авария · 80 мин · автомобиль · как можно скорее». */
export function workTypeSummary(form: UrgentForm): string {
  const parts = [
    form.workType?.title ?? SKILL_LABELS[form.skill],
    `${form.durationMin} мин`,
    form.transport === '' ? 'любой транспорт' : TRANSPORT_LABELS[form.transport].toLowerCase(),
  ];
  if (form.needsEquipment) parts.push('с оборудованием');
  parts.push(form.asap ? 'как можно скорее' : `окно ${formatWindow(form.windowStart, form.windowEnd)}`);
  return parts.join(' · ');
}

/** Форма изменения заявки. Время события хранится отдельно: оно не часть заявки. */
export interface RequestEditForm {
  address: string;
  /** Точка, указанная на карте в этом диалоге; null, если диспетчер точку не указывал. */
  point: PickedPoint | null;
  windowStart: HHMM;
  windowEnd: HHMM;
  durationMin: number;
  skill: Skill;
  priority: Priority;
  transport: Transport | '';
  /** Как можно скорее: поля окна скрыты, окно задаёт сервер. */
  asap: boolean;
  /** Инженер везёт с собой единицу оборудования: роутер, приставку или колонку. */
  needsEquipment: boolean;
}

type VisitFields = Pick<UrgentForm, 'address' | 'point' | 'windowStart' | 'windowEnd' | 'durationMin' | 'asap'>;

/** Время события проверяется только по формату: событие ставится на шкалу дня в любое время, и раньше часов тоже. */
export function timeError(time: string): string | null {
  return isValidTime(time) ? null : 'Укажите время в формате ЧЧ:ММ';
}

/**
 * Подсказка в диалогах события раньше всех смен. Пересчитает ли сервер план, решает результат (окно выбора
 * открывается, только если «Ничего не менять» ломает больше пересчёта), а если пересчитает — то весь день.
 */
export const BEFORE_SHIFTS_HINT = 'Событие до начала смен: пересчёт, если понадобится, захватит весь день';

/** Подсказка для события раньше самой ранней смены доступных инженеров; null, если событие внутри дня или время неверное. */
export function beforeShiftsHint(time: string, engineers: Engineer[]): string | null {
  const dayStart = earliestShiftStart(engineers);
  if (dayStart === null || !isValidTime(time)) return null;
  return toMinutes(time) < toMinutes(dayStart) ? BEFORE_SHIFTS_HINT : null;
}

/**
 * Проверки окна визита; окно заявки «как можно скорее» задаёт сервер, его поля скрыты и не проверяются.
 * С сеткой окно должно быть её слотом, и слот не должен быть уже прошедшим, — то же самое проверяет сервер.
 * Без сетки окно вводится временем, как раньше, и прошедшее окно отклоняет только сервер.
 */
function windowErrors({ asap, windowStart, windowEnd }: VisitFields, grid: TimeSlot[], time: HHMM): string[] {
  if (asap) return [];
  if (!isValidTime(windowStart) || !isValidTime(windowEnd)) return ['Укажите окно визита в формате ЧЧ:ММ'];
  if (offGrid(grid, windowStart, windowEnd)) return [offGridError(grid)];
  if (grid.length > 0 && windowPassed(windowEnd, time)) return [passedWindowError(windowStart, windowEnd)];
  return toMinutes(windowEnd) <= toMinutes(windowStart) ? ['Конец окна должен быть позже начала'] : [];
}

/** Общие проверки места, окна и длительности визита для срочной и изменённой заявки; time — время события. */
function visitErrors(form: VisitFields, grid: TimeSlot[], time: HHMM): string[] {
  const errors: string[] = [];
  if (!form.address.trim() && !form.point) errors.push('Укажите адрес или точку на карте');
  errors.push(...windowErrors(form, grid, time));
  if (!Number.isFinite(form.durationMin) || form.durationMin <= 0) errors.push('Длительность должна быть больше нуля');
  return errors;
}

export function validateUrgentForm(form: UrgentForm, grid: TimeSlot[] = []): string[] {
  const errors = visitErrors(form, grid, form.time);
  const time = timeError(form.time);
  if (time) errors.push(time);
  return errors;
}

/** Самое раннее начало смены среди доступных инженеров; null, если доступных нет. */
export function earliestShiftStart(engineers: Engineer[]): HHMM | null {
  let earliest: HHMM | null = null;
  for (const engineer of engineers) {
    if (!engineer.available) continue;
    if (earliest === null || toMinutes(engineer.shift_start) < toMinutes(earliest)) earliest = engineer.shift_start;
  }
  return earliest;
}

/** Самый поздний конец смены среди доступных инженеров; null, если доступных нет. */
export function latestShiftEnd(engineers: Engineer[]): HHMM | null {
  let latest: HHMM | null = null;
  for (const engineer of engineers) {
    if (!engineer.available) continue;
    if (latest === null || toMinutes(engineer.shift_end) > toMinutes(latest)) latest = engineer.shift_end;
  }
  return latest;
}

/**
 * Окно срочной заявки по умолчанию: ближайший слот сетки, в который бригада ещё успевает с работами на
 * durationMin. Слот, где до конца осталось меньше, чем идут работы, клиенту не называют: в него не приехать,
 * и заявка осталась бы без инженера — поэтому в 13:50 предлагается 14:00–16:00, а не доживающий 12:00–14:00.
 * Без сетки (старый сервер, офлайн) окно прежнее: с времени события, но не раньше начала смен, длиной два часа.
 */
export function defaultUrgentWindow(
  time: HHMM,
  engineers: Engineer[],
  grid: TimeSlot[] = [],
  durationMin: number = URGENT_WINDOW_MIN,
): Pick<UrgentForm, 'windowStart' | 'windowEnd'> {
  const dayStart = earliestShiftStart(engineers);
  const windowStart = dayStart === null ? time : laterTime(time, dayStart);
  const slot = defaultSlot(grid, windowStart, durationMin);
  if (slot) return { windowStart: slot.start, windowEnd: slot.end };
  return { windowStart, windowEnd: addMinutes(windowStart, URGENT_WINDOW_MIN) };
}

/** Сколько визитов инженера в плане начинаются в указанное время или позже. */
export function visitsFrom(plan: Plan, engineerId: string, time: HHMM): number {
  const from = toMinutes(time);
  const route = plan.routes.find((item) => item.engineer_id === engineerId);
  return route ? route.visits.filter((visit) => toMinutes(visit.start) >= from).length : 0;
}

/** Доступный инженер с наибольшим числом визитов после указанного времени; при равенстве первый по имени. */
export function busiestEngineerId(engineers: Engineer[], plan: Plan, time: HHMM): string | null {
  const ranked = engineers
    .filter((engineer) => engineer.available)
    .map((engineer) => ({ engineer, visits: visitsFrom(plan, engineer.id, time) }))
    .sort((a, b) => b.visits - a.visits || a.engineer.name.localeCompare(b.engineer.name, 'ru'));
  return ranked[0]?.engineer.id ?? null;
}

/** Транспорт, который предлагаем при смене: без машины обычно пересаживаются на велосипед, иначе выдают машину. */
export function defaultNewTransport(current: Transport): Transport {
  return current === 'car' ? 'bike' : 'car';
}

/** Сколько визитов инженера, начинающихся в указанное время или позже, требуют автомобиль. */
export function carRequiredVisitsFrom(plan: Plan, requests: ServiceRequest[], engineerId: string, time: HHMM): number {
  const carOnly = new Set(requests.filter((request) => request.transport_required === 'car').map((request) => request.id));
  const from = toMinutes(time);
  const route = plan.routes.find((item) => item.engineer_id === engineerId);
  return route ? route.visits.filter((visit) => toMinutes(visit.start) >= from && carOnly.has(visit.request_id)).length : 0;
}

/** Подсказка диспетчеру, когда инженер остаётся без машины, а в его маршруте есть заявки только для автомобиля. */
export function carDowngradeHint(count: number, time: HHMM): string {
  return `Заявок с требованием «${TRANSPORT_LABELS.car}» после ${time}: ${count}, их перераспределит оптимизатор`;
}

/**
 * Работа по заявке уже началась к времени на часах: визит закреплён в плане или начался раньше часов, и заявка не отменена.
 * Правило то же, что у сервера: визит, который начинается ровно в это время, ещё не начат.
 * Такую заявку нельзя ни отменить, ни изменить; визит, к которому инженер только едет, не закреплён.
 */
export function isWorkStarted(request: ServiceRequest, visit: Visit | undefined, clock: HHMM): boolean {
  if (!visit || request.status === 'cancelled') return false;
  return visit.pinned || toMinutes(visit.start) < toMinutes(clock);
}

export interface RequestActionContext {
  busy: boolean;
  /** Время на часах шкалы дня: в это время ставится отмена. */
  clock: HHMM;
}

/**
 * Кнопки «Изменить» и «Отменить» у заявки: одно правило для списка заявок, карточки заявки и «Коммуникаций».
 * Кнопки «Вернуть» у отменённой заявки нет: передумать можно только в уведомлении сразу после отмены.
 */
export interface RequestActionState {
  /** Заявка отменена: кнопки «Отменить» у неё нет. */
  cancelled: boolean;
  /** Кнопки недоступны: идёт перепланирование или работа уже началась. */
  disabled: boolean;
  editTitle: string | undefined;
  cancelTitle: string | undefined;
  /** Что отправит кнопка «Отменить»: время часов в момент клика. */
  cancelEvent: PlanEvent;
}

/** Правило кнопок заявки; visit берётся из текущего плана, потому что события меняют именно его. */
export function requestActionState(
  request: ServiceRequest,
  visit: Visit | undefined,
  { busy, clock }: RequestActionContext,
): RequestActionState {
  const cancelled = request.status === 'cancelled';
  const started = isWorkStarted(request, visit, clock);
  return {
    cancelled,
    disabled: busy || started,
    editTitle: started ? 'Работа уже началась, изменить нельзя' : undefined,
    cancelTitle: started ? 'Работа уже началась, отменить нельзя' : undefined,
    cancelEvent: cancelEvent(request.id, clock),
  };
}

/** Выбор бригады в карточке заявки: доступен ли он и почему нет. */
export interface ReassignState {
  disabled: boolean;
  title: string | undefined;
}

/**
 * Правило выбора бригады: как у кнопок заявки, и ещё отменённую, перенесённую и заявку без точки на карте
 * сервер никому не назначит. Перенесённую вернёт в работу дня только новый звонок: клиенту сказали, что сегодня
 * не приедем.
 */
export function reassignState(request: ServiceRequest, visit: Visit | undefined, context: RequestActionContext): ReassignState {
  if (request.status === 'cancelled') return { disabled: true, title: 'Заявка отменена' };
  if (request.status === 'postponed') return { disabled: true, title: 'Заявка перенесена: клиенту сказали, что сегодня не приедем' };
  if (isWorkStarted(request, visit, context.clock)) return { disabled: true, title: 'Работа уже началась, переназначить нельзя' };
  if (request.lat === null || request.lon === null) return { disabled: true, title: 'Адрес не найден на карте, назначить бригаду нельзя' };
  return { disabled: requestActionState(request, visit, context).disabled, title: undefined };
}

/** Кнопка «Задержка бригады» в карточке заявки: чья бригада и доступна ли кнопка. */
export interface RequestDelayState {
  /** Бригада визита заявки в текущем плане. */
  engineerId: string;
  /** Идёт перепланирование или бригада уже недоступна: сервер задержку не примет. */
  disabled: boolean;
  title: string | undefined;
}

/**
 * Задержка бригады из карточки заявки: диспетчер думает «на Окской работа затянулась на час», а не «бригада опаздывает».
 * Кнопка есть, только пока по часам визит «В работе» или бригада «В пути» к нему — те же статусы, что у метки в карточке.
 * «Задержка с» в обоих случаях — время часов, как со страницы бригады: событие применяется сразу, с прогнозом опозданий
 * и правилом окна. В работе сервер продлевает начатый визит на N минут, в пути бригада на N минут позже приезжает;
 * следующие визиты сдвигаются. Задержка на плановый конец визита ждала бы на шкале, пока до него дойдут часы, и сам
 * визит не продлила бы. null — кнопки нет: визит впереди (для него есть «Изменить» длительность), выполнен, бригада
 * ждёт у клиента, заявка без бригады или отменена.
 */
export function requestDelayState(
  request: ServiceRequest,
  assignment: { engineerId: string; visit: Visit } | undefined,
  engineers: Engineer[],
  { busy, clock }: RequestActionContext,
): RequestDelayState | null {
  if (!assignment || request.status === 'cancelled') return null;
  const { engineerId, visit } = assignment;
  const status = requestClockStatus(visit, clock);
  if (status !== 'working' && status !== 'driving') return null;
  const unavailable = engineers.find((engineer) => engineer.id === engineerId)?.available === false;
  return {
    engineerId,
    disabled: busy || unavailable,
    // Та же подсказка, что у «Задержки» на странице бригады.
    title: unavailable ? 'Инженер недоступен, задержку поставить нельзя' : undefined,
  };
}

/**
 * Почему бригада не может взять заявку, коротко для списка бригад; null — может.
 * Правила те же, что у сервера: доступность, навык и транспорт. Успеет ли бригада в окно и смену, проверяет только сервер.
 */
export function brigadeIneligibility(request: ServiceRequest, engineer: Engineer): string | null {
  if (!engineer.available) return engineer.unavailable_from ? `недоступна с ${engineer.unavailable_from}` : 'недоступна';
  if (!engineer.skills.includes(request.skill)) return `нет навыка «${SKILL_LABELS[request.skill]}»`;
  if (request.transport_required !== null && request.transport_required !== engineer.transport) {
    return `нужен транспорт «${TRANSPORT_LABELS[request.transport_required]}»`;
  }
  return null;
}

/** Бригада в списке выбора бригады заявки. */
export interface BrigadeOption {
  engineer: Engineer;
  /** Заявка у этой бригады в текущем плане. */
  current: boolean;
  /** Бригада не может взять заявку. Бригада текущего плана доступна всегда: её выбор просто закрывает список. */
  disabled: boolean;
  /** Подпись справа: «в плане», почему бригада не подходит или сколько у неё заявок в плане. */
  note: string;
}

/**
 * Все бригады в порядке набора данных для выбора бригады заявки по текущему плану.
 * currentId задаёт бригаду заявки, когда она не из этого плана: например, из варианта «Оптимально по дню».
 */
export function brigadeOptions(request: ServiceRequest, engineers: Engineer[], plan: Plan, currentId?: string | null): BrigadeOption[] {
  const visits = new Map(plan.routes.map((route) => [route.engineer_id, route.visits.length]));
  const holderId =
    currentId !== undefined
      ? currentId
      : (plan.routes.find((route) => route.visits.some((visit) => visit.request_id === request.id))?.engineer_id ?? null);
  return engineers.map((engineer) => {
    const current = engineer.id === holderId;
    const reason = current ? null : brigadeIneligibility(request, engineer);
    const count = visits.get(engineer.id) ?? 0;
    const note = current ? 'в плане' : (reason ?? `${count} ${plural(count, 'заявка', 'заявки', 'заявок')}`);
    return { engineer, current, disabled: reason !== null, note };
  });
}

/** Переназначение заявки на бригаду: прежнюю бригаду сервер запишет сам. */
export const reassignEvent = (requestId: string, engineerId: string, time: HHMM): PlanEvent => ({
  type: 'request_reassigned',
  time,
  request: null,
  request_id: requestId,
  engineer_id: engineerId,
});

export function newUrgentId(timestamp: number): string {
  return `URG-${timestamp.toString(36).toUpperCase()}`;
}

const pointAddress = (point: PickedPoint) => `Точка на карте ${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}`;

/**
 * Срочная заявка из диалога. У заявки «как можно скорее» окно от времени события до самого позднего конца смен
 * доступных инженеров: сервер задаёт такое окно сам, введённое в скрытых полях не отправляется.
 */
export function buildUrgentEvent(form: UrgentForm, requestId: string, engineers: Engineer[]): PlanEvent {
  const point = form.point;
  const visitWindow = form.asap
    ? { window_start: form.time, window_end: latestShiftEnd(engineers) ?? form.time }
    : { window_start: form.windowStart, window_end: form.windowEnd };
  const address = form.address.trim() || (point ? pointAddress(point) : 'Срочная заявка');
  const request: ServiceRequest = {
    id: requestId,
    address,
    lat: point?.lat ?? null,
    lon: point?.lon ?? null,
    geocode_precision: point ? 'house' : 'none',
    district: '',
    duration_min: Math.round(form.durationMin),
    ...visitWindow,
    asap: form.asap,
    priority: 'urgent',
    // Уровень — род работ, его ставит тип работ (сервер ставит тот же сам); без типов от сервера заявка — авария.
    // В очереди распределения срочная заявка любого типа и так наравне с аварией: это делает priority «urgent».
    tier: form.workType?.tier ?? 'emergency',
    skill: form.skill,
    transport_required: form.transport === '' ? null : form.transport,
    status: 'active',
    // Тип работ уходит с заявкой так же, как у заявок дня; без типов от сервера — прежняя отметка.
    source_type_bk: form.workType?.source_type_bk ?? 'Срочная заявка диспетчера',
    source_type_hd: form.workType?.source_type_hd ?? '',
    needs_equipment: form.needsEquipment,
  };
  return { type: 'urgent', time: form.time, request, request_id: null, engineer_id: null };
}

/** Форма изменения, заполненная значениями сохранённой заявки. */
export function requestEditForm(request: ServiceRequest): RequestEditForm {
  return {
    address: request.address,
    point: null,
    windowStart: request.window_start,
    windowEnd: request.window_end,
    durationMin: request.duration_min,
    skill: request.skill,
    priority: request.priority,
    transport: request.transport_required ?? '',
    asap: request.asap,
    needsEquipment: request.needs_equipment,
  };
}

/**
 * Заявка после изменения. Номер, статус, район и типы из источника остаются от сохранённой заявки.
 * Координаты: точка с карты, если её указали; для нового адреса без точки null, чтобы сервер нашёл адрес сам;
 * иначе прежние.
 */
export function updatedRequest(original: ServiceRequest, form: RequestEditForm): ServiceRequest {
  const typed = form.address.trim();
  const sameAddress = typed === original.address.trim();
  const { point } = form;
  const address = sameAddress ? original.address : typed || (point ? pointAddress(point) : '');
  const location: Pick<ServiceRequest, 'lat' | 'lon' | 'geocode_precision'> = point
    ? { lat: point.lat, lon: point.lon, geocode_precision: 'house' }
    : sameAddress
      ? { lat: original.lat, lon: original.lon, geocode_precision: original.geocode_precision }
      : { lat: null, lon: null, geocode_precision: 'none' };
  return {
    ...original,
    address,
    ...location,
    duration_min: Math.round(form.durationMin),
    // Окно заявки «как можно скорее» задаёт сервер: отправляем сохранённое, скрытые поля окна ни на что не влияют.
    window_start: form.asap ? original.window_start : form.windowStart,
    window_end: form.asap ? original.window_end : form.windowEnd,
    asap: form.asap,
    priority: form.priority,
    skill: form.skill,
    transport_required: form.transport === '' ? null : form.transport,
    needs_equipment: form.needsEquipment,
  };
}

/** Изменение заявки: клиент отправляет заявку целиком с тем же номером, прежнюю версию сервер запишет сам. */
export function requestUpdateEvent(original: ServiceRequest, form: RequestEditForm, time: HHMM): PlanEvent {
  return {
    type: 'request_updated',
    time,
    request: updatedRequest(original, form),
    request_id: original.id,
    engineer_id: null,
  };
}

const transportRequirement = (transport: Transport | null) => (transport ? TRANSPORT_LABELS[transport] : 'не требуется');

/** Что поменялось в заявке, языком диспетчера и всегда в одном порядке. */
export function requestChanges(prev: ServiceRequest, next: ServiceRequest): string[] {
  const changes: string[] = [];
  if (next.address !== prev.address) {
    changes.push(`адрес ${shortAddress(prev.address)} → ${shortAddress(next.address)}`);
  } else if (next.lat !== null && next.lon !== null && (next.lat !== prev.lat || next.lon !== prev.lon)) {
    // Координаты null при том же адресе сервер заменит прежними, поэтому новой точкой считаем только заданные.
    changes.push('точка на карте');
  }
  if (next.asap !== prev.asap) {
    // Окно заявки «как можно скорее» задаёт сервер, поэтому вместо окна называем смену режима.
    changes.push(next.asap ? 'как можно скорее' : 'окно вместо «как можно скорее»');
  } else if (!next.asap && (next.window_start !== prev.window_start || next.window_end !== prev.window_end)) {
    changes.push(`окно ${formatWindow(prev.window_start, prev.window_end)} → ${formatWindow(next.window_start, next.window_end)}`);
  }
  if (next.duration_min !== prev.duration_min) changes.push(`длительность ${prev.duration_min} → ${next.duration_min} мин`);
  if (next.skill !== prev.skill) changes.push(`навык ${SKILL_LABELS[prev.skill]} → ${SKILL_LABELS[next.skill]}`);
  if (next.priority !== prev.priority) changes.push(`приоритет ${PRIORITY_LABELS[prev.priority]} → ${PRIORITY_LABELS[next.priority]}`);
  if (next.transport_required !== prev.transport_required) {
    changes.push(`транспорт ${transportRequirement(prev.transport_required)} → ${transportRequirement(next.transport_required)}`);
  }
  if (next.needs_equipment !== prev.needs_equipment) changes.push(next.needs_equipment ? 'оборудование нужно' : 'оборудование не нужно');
  return changes;
}

/**
 * Проверки формы изменения: как у срочной заявки и отказ, если в заявке ничего не поменялось.
 * Окно, которого диспетчер не трогал, сеткой не проверяется: у заявки из данных оно своё (у аварии там весь день),
 * и менять длительность или адрес такой заявки это не мешает. Ровно так же смотрит на окно и сервер.
 * Снятое «как можно скорее» — это всегда новое окно, даже если поля остались прежними: окно такой заявке задал
 * сервер, клиенту его не называли, и обещанием оно стать не может, пока диспетчер не выбрал слот.
 */
export function validateRequestEdit(
  original: ServiceRequest,
  form: RequestEditForm,
  time: HHMM,
  grid: TimeSlot[] = [],
): string[] {
  const kept =
    form.asap === original.asap && form.windowStart === original.window_start && form.windowEnd === original.window_end;
  const errors = visitErrors(form, kept ? [] : grid, time);
  const timeProblem = timeError(time);
  if (timeProblem) errors.push(timeProblem);
  else if (requestChanges(original, updatedRequest(original, form)).length === 0) errors.push('Ничего не изменилось');
  return errors;
}

export const cancelEvent = (requestId: string, time: HHMM): PlanEvent => ({
  type: 'cancel',
  time,
  request: null,
  request_id: requestId,
  engineer_id: null,
});

export const unavailableEvent = (engineerId: string, time: HHMM): PlanEvent => ({
  type: 'engineer_unavailable',
  time,
  request: null,
  request_id: null,
  engineer_id: engineerId,
});

/** Смена транспорта: клиент передаёт только новый транспорт, прежний сервер берёт у инженера сам. */
export const transportChangeEvent = (engineerId: string, transport: Transport, time: HHMM): PlanEvent => ({
  type: 'engineer_transport_changed',
  time,
  request: null,
  request_id: null,
  engineer_id: engineerId,
  transport,
});

/** Быстрый выбор задержки в диалоге, минут. */
export const DELAY_PRESETS = [15, 30, 60];
export const MIN_DELAY_MIN = 5;
export const MAX_DELAY_MIN = 480;

/** Задержка принимается целым числом минут в тех же границах, что проверяет сервер. */
export function validateDelay(value: number): string | null {
  if (!Number.isInteger(value) || value < MIN_DELAY_MIN || value > MAX_DELAY_MIN) {
    return `Задержка должна быть от ${MIN_DELAY_MIN} до ${MAX_DELAY_MIN} минут`;
  }
  return null;
}

/** Задержка инженера: клиент передаёт, кто задерживается и на сколько минут; последствия сервер считает по плану. */
export const delayEvent = (engineerId: string, delayMin: number, time: HHMM): PlanEvent => ({
  type: 'engineer_delayed',
  time,
  request: null,
  request_id: null,
  engineer_id: engineerId,
  delay_min: delayMin,
});

/**
 * Прогноз задержки без перепланирования языком диспетчера: к скольким клиентам и насколько опоздаем, и переработка.
 * kept — задержка применена с «Ничего не менять» (выбрал диспетчер или проход шкалы, когда пересчёт ничего не
 * спасал): план и есть план без перепланирования, прогноз в нём сбылся, и говорим о нём как о факте, без «бы».
 * Заявки и инженеры в тексте пока не нужны, параметры оставлены по общему контракту описаний событий.
 */
export function forecastLines(
  forecast: DelayForecast,
  _requests: Map<string, ServiceRequest>,
  _engineers: Map<string, Engineer>,
  kept = false,
): string[] {
  const late = forecast.late_without_replan;
  const overtime = forecast.overtime_without_replan_min;
  const overtimeText = `переработка ${overtime} мин`;
  if (late.length === 0) {
    if (kept) return [overtime > 0 ? `Переработка ${overtime} мин` : 'Задержка не привела к опозданиям'];
    return [overtime > 0 ? `Без перепланирования была бы ${overtimeText}` : 'Задержка не привела бы к опозданиям'];
  }
  const minutes = late.map((item) => item.late_min);
  const least = Math.min(...minutes);
  const most = Math.max(...minutes);
  const range = least === most ? `${least}` : `${least}–${most}`;
  const clients = `${late.length} ${plural(late.length, 'клиенту', 'клиентам', 'клиентам')}`;
  const line = kept ? `Опоздаем к ${clients} на ${range} мин` : `Без перепланирования опоздали бы к ${clients} на ${range} мин`;
  return [overtime > 0 ? `${line} и ${overtimeText}` : line];
}

/** Подробности прогноза для подсказки: по строке на каждый визит с опозданием. */
export function lateVisitsTitle(forecast: DelayForecast, requests: Map<string, ServiceRequest>): string {
  return forecast.late_without_replan
    .map(
      (item) =>
        `${requestLabelOf(item.request_id, requests)}: план ${item.planned_start}, прогноз ${item.forecast_start}, +${item.late_min} мин`,
    )
    .join('\n');
}

/**
 * Заявка, о которой событие: у срочной заявки — новая заявка, у отмены, возврата, изменения, переназначения
 * и звонка клиенту — названная.
 */
export function eventRequestId(event: PlanEvent): string | null {
  switch (event.type) {
    case 'urgent':
      return event.request?.id ?? null;
    case 'cancel':
    case 'restore':
    case 'request_updated':
    case 'request_reassigned':
    case 'client_agreed':
      return event.request_id ?? event.request?.id ?? null;
    default:
      return null;
  }
}

/**
 * Номер заявки события с приставкой срочности: у события со своей заявкой приоритет берём из неё,
 * у отмены, возврата и переназначения — из заявок дня.
 */
function eventRequestLabel(event: PlanEvent, requests: Map<string, ServiceRequest>): string {
  const id = eventRequestId(event) ?? '';
  return requestLabel(id, event.request?.priority ?? requests.get(id)?.priority);
}

export function describeEvent(event: PlanEvent, engineers: Map<string, Engineer>, requests: Map<string, ServiceRequest>): string {
  switch (event.type) {
    case 'engineer_delayed': {
      const name = engineers.get(event.engineer_id ?? '')?.name ?? event.engineer_id;
      if (event.delay_min == null) return `Задержка: ${name} с ${event.time}`;
      return `Задержка: ${name} на ${event.delay_min} мин с ${event.time}`;
    }
    case 'request_updated': {
      const title = `Изменена заявка ${eventRequestLabel(event, requests)} с ${event.time}`;
      const changes = event.previous_request && event.request ? requestChanges(event.previous_request, event.request) : [];
      return changes.length > 0 ? `${title}: ${changes.join(', ')}` : title;
    }
    case 'engineer_transport_changed': {
      const name = engineers.get(event.engineer_id ?? '')?.name ?? event.engineer_id;
      const next = event.transport ? TRANSPORT_LABELS[event.transport] : 'другой транспорт';
      if (event.previous_transport) {
        return `Смена транспорта: ${name}, ${TRANSPORT_LABELS[event.previous_transport]} → ${next} с ${event.time}`;
      }
      return `Смена транспорта: ${name} на ${next} с ${event.time}`;
    }
    case 'urgent':
      if (event.request?.asap) return `Срочная заявка ${eventRequestLabel(event, requests)} как можно скорее, ${event.time}`;
      return `Срочная заявка ${eventRequestLabel(event, requests)} в ${event.time}`;
    case 'cancel':
      return `Отмена заявки ${eventRequestLabel(event, requests)} в ${event.time}`;
    // Возврата в интерфейсе больше нет, но он есть в днях, сохранённых раньше, и в API: шкала подписывает его как прежде.
    case 'restore':
      return `Возврат заявки ${eventRequestLabel(event, requests)} в ${event.time}`;
    case 'engineer_unavailable':
      return `Инженер недоступен: ${engineers.get(event.engineer_id ?? '')?.name ?? event.engineer_id} с ${event.time}`;
    case 'request_reassigned': {
      const name = engineers.get(event.engineer_id ?? '')?.name ?? event.engineer_id;
      const title = `Переназначение заявки ${eventRequestLabel(event, requests)}`;
      // Прежнюю бригаду сервер записывает у применённого события; у заявки без бригады и у события клиента её нет.
      // Имя бригады не склоняем: стрелка показывает, к какой бригаде уходит заявка.
      if (!event.previous_engineer_id) return `${title} → ${name} с ${event.time}`;
      const previous = engineers.get(event.previous_engineer_id)?.name ?? event.previous_engineer_id;
      return `${title}: ${previous} → ${name} с ${event.time}`;
    }
    case 'client_agreed': {
      // Что клиенту сказали: окно, которое стало окном заявки, или что сегодня не приедем.
      const told = event.agreed_window;
      const what = told
        ? requestWindowPhrase({ asap: told.asap ?? false, window_start: told.start, window_end: told.end })
        : 'сегодня не приедем';
      return `Клиент ${eventRequestLabel(event, requests)}: ${what}, звонок в ${event.time}`;
    }
  }
}
