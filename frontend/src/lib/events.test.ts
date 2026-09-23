import { describe, expect, it } from 'vitest';
import type { PlanEvent, ServiceRequest } from '../api/types';
import {
  makeAsapRequest,
  makeDataUrgentState,
  makeDelayEvent,
  makeDelayForecast,
  makePlanningState,
  makeReassignEvent,
  makeRequestUpdateEvent,
  WINDOW_GRID,
  WORK_TYPE_EVENTS,
  WORK_TYPES,
  workTypeOf,
} from '../test/fixtures';
import {
  BEFORE_SHIFTS_HINT,
  beforeShiftsHint,
  brigadeIneligibility,
  brigadeOptions,
  buildUrgentEvent,
  busiestEngineerId,
  cancelEvent,
  carRequiredVisitsFrom,
  defaultNewTransport,
  defaultUrgentWindow,
  DELAY_PRESETS,
  delayEvent,
  describeEvent,
  earliestShiftStart,
  eventRequestId,
  forecastLines,
  isWorkStarted,
  latestShiftEnd,
  newUrgentId,
  reassignEvent,
  reassignState,
  requestActionState,
  requestChanges,
  requestEditForm,
  requestUpdateEvent,
  timeError,
  transportChangeEvent,
  unavailableEvent,
  updatedRequest,
  validateDelay,
  validateRequestEdit,
  validateUrgentForm,
  visitsFrom,
  workTypeFields,
  workTypeSummary,
  type UrgentForm,
} from './events';
import { EVENT_LABELS } from './format';
import { assignmentIndex, byId } from './planView';

const form: UrgentForm = {
  workType: null,
  address: 'Город Москва, ул.Ташкентская, д. 16к2',
  point: null,
  windowStart: '13:00',
  windowEnd: '15:00',
  durationMin: 60,
  skill: 'emergency',
  transport: 'car',
  time: '13:00',
  asap: false,
  needsEquipment: false,
};

describe('events', () => {
  it('accepts a valid urgent form at any time of the day', () => {
    expect(validateUrgentForm(form)).toEqual([]);
    expect(validateUrgentForm({ ...form, time: '00:05' })).toEqual([]);
  });

  it('reports every problem in Russian', () => {
    const errors = validateUrgentForm({ ...form, address: ' ', windowEnd: '12:00', durationMin: 0, time: '9' });
    expect(errors).toEqual([
      'Укажите адрес или точку на карте',
      'Конец окна должен быть позже начала',
      'Длительность должна быть больше нуля',
      'Укажите время в формате ЧЧ:ММ',
    ]);
    expect(timeError('9')).toBe('Укажите время в формате ЧЧ:ММ');
    expect(timeError('07:45')).toBeNull();
  });

  it('warns that an event before the first shift replans the whole day', () => {
    const { engineers } = makePlanningState();
    expect(beforeShiftsHint('09:59', engineers)).toBe(BEFORE_SHIFTS_HINT);
    expect(BEFORE_SHIFTS_HINT).toBe('Событие до начала смен: план дня пересчитается целиком');
    expect(beforeShiftsHint('10:00', engineers)).toBeNull();
    expect(beforeShiftsHint('9', engineers)).toBeNull();
    expect(beforeShiftsHint('00:00', [])).toBeNull();
  });

  it('builds an urgent event with a full request from a map point', () => {
    const event = buildUrgentEvent({ ...form, address: '', point: { lat: 55.71, lon: 37.8 }, transport: '' }, 'URG-TEST', makePlanningState().engineers);
    expect(event).toMatchObject({ type: 'urgent', time: '13:00', request_id: null, engineer_id: null });
    expect(event.request).toMatchObject({
      id: 'URG-TEST',
      lat: 55.71,
      lon: 37.8,
      priority: 'urgent',
      status: 'active',
      transport_required: null,
      geocode_precision: 'house',
      window_start: '13:00',
      window_end: '15:00',
      asap: false,
      needs_equipment: false,
    });
    expect(event.request?.address).toBe('Точка на карте 55.71000, 37.80000');
  });

  it('ставит срочной заявке диспетчера без типа работ верхний уровень распределения', () => {
    const { engineers } = makePlanningState();
    // Сервер ставит уровень сам, но и клиент отправляет тот же: в списке заявка сразу как авария.
    expect(buildUrgentEvent(form, 'URG-TIER', engineers).request).toMatchObject({ tier: 'emergency', priority: 'urgent' });
  });

  it('несёт в срочной заявке отметку об оборудовании', () => {
    const { engineers } = makePlanningState();
    expect(buildUrgentEvent({ ...form, needsEquipment: true }, 'URG-EQ', engineers).request).toMatchObject({ needs_equipment: true });
  });

  it('sends an urgent request as soon as possible from the event time to the latest shift end', () => {
    const { engineers } = makePlanningState();
    const asap: UrgentForm = { ...form, asap: true, time: '13:20', windowStart: '15:00', windowEnd: '16:00' };
    expect(buildUrgentEvent(asap, 'URG-ASAP', engineers).request).toMatchObject({ asap: true, window_start: '13:20', window_end: '22:00' });
    expect(buildUrgentEvent(asap, 'URG-ASAP', []).request).toMatchObject({ asap: true, window_start: '13:20', window_end: '13:20' });
    expect(buildUrgentEvent(form, 'URG-WINDOW', engineers).request).toMatchObject({ asap: false, window_start: '13:00', window_end: '15:00' });
  });

  it('fills the urgent request from the norms of every work type the server sends', () => {
    expect(WORK_TYPES.map((workType) => [workType.title, workTypeFields(workType)])).toEqual([
      [
        'Авария',
        { workType: WORK_TYPES[0], skill: 'emergency', durationMin: 80, transport: 'car', needsEquipment: false, asap: true },
      ],
      [
        'Подключение',
        { workType: WORK_TYPES[1], skill: 'connection', durationMin: 70, transport: '', needsEquipment: true, asap: false },
      ],
      [
        'Ремонт у клиента',
        { workType: WORK_TYPES[2], skill: 'local', durationMin: 30, transport: '', needsEquipment: false, asap: false },
      ],
      [
        'Дозаказ оборудования',
        { workType: WORK_TYPES[3], skill: 'connection', durationMin: 20, transport: 'car', needsEquipment: true, asap: false },
      ],
    ]);
  });

  it('builds for every work type exactly the event the backend test posts to the server', () => {
    // Эталон общий с backend/tests/test_work_types.py: сервер принимает ровно эти события.
    const { engineers } = makePlanningState();
    const place = { address: 'Город Москва, ул.Таганская, д. 1', point: { lat: 55.755, lon: 37.61 }, windowStart: '14:00', windowEnd: '16:00', time: '13:00' };
    const events = WORK_TYPES.map((workType, index) =>
      buildUrgentEvent({ ...place, ...workTypeFields(workType) }, `URG-GOLDEN${index + 1}`, engineers),
    );
    expect(events).toEqual(WORK_TYPE_EVENTS);
    // Тип работ уходит с заявкой, как у заявок дня, и с уровнем своего типа BK, как его ставит сервер;
    // срочной заявку делает priority «urgent» при любом типе.
    expect(
      events.map((event) => [event.request?.source_type_bk, event.request?.source_type_hd, event.request?.tier, event.request?.priority]),
    ).toEqual([
      ['Глобальная проблема', 'Авария', 'emergency', 'urgent'],
      ['Подключение', 'Заявка на подключение', 'connection', 'urgent'],
      ['Локальная заявка', '', 'routine', 'urgent'],
      ['Дозаказ', 'Дозаказ оборудования', 'routine', 'urgent'],
    ]);
  });

  it('keeps the old source mark of an urgent request without work types from the server', () => {
    const { engineers } = makePlanningState();
    expect(buildUrgentEvent(form, 'URG-OLD', engineers).request).toMatchObject({ source_type_bk: 'Срочная заявка диспетчера', source_type_hd: '' });
  });

  it('sums up what the work type filled in one line', () => {
    const emergency = { ...form, ...workTypeFields(workTypeOf('Глобальная проблема')) };
    expect(workTypeSummary(emergency)).toBe('Авария · 80 мин · автомобиль · как можно скорее');
    const extra = { ...form, ...workTypeFields(workTypeOf('Дозаказ')), windowStart: '14:00', windowEnd: '16:00' };
    expect(workTypeSummary(extra)).toBe('Дозаказ оборудования · 20 мин · автомобиль · с оборудованием · окно 14:00–16:00');
    expect(workTypeSummary({ ...form, transport: '' })).toBe('Аварийные работы · 60 мин · любой транспорт · окно 13:00–15:00');
  });

  it('skips the window checks of an urgent request as soon as possible', () => {
    expect(validateUrgentForm({ ...form, asap: true, windowStart: '', windowEnd: '' })).toEqual([]);
    expect(validateUrgentForm({ ...form, asap: true, address: ' ', windowStart: '14:00', windowEnd: '12:00', time: '' })).toEqual([
      'Укажите адрес или точку на карте',
      'Укажите время в формате ЧЧ:ММ',
    ]);
  });

  it('finds the latest shift end among available engineers', () => {
    const { engineers } = makePlanningState();
    const withShiftEnd = (id: string, shiftEnd: string) =>
      engineers.map((engineer) => (engineer.id === id ? { ...engineer, shift_end: shiftEnd } : engineer));
    expect(latestShiftEnd(engineers)).toBe('22:00');
    expect(latestShiftEnd(withShiftEnd('E02', '23:30'))).toBe('23:30');
    expect(latestShiftEnd(withShiftEnd('E03', '23:59'))).toBe('22:00');
    expect(latestShiftEnd(engineers.map((engineer) => ({ ...engineer, available: false })))).toBeNull();
    expect(latestShiftEnd([])).toBeNull();
  });

  it('builds cancel and unavailability events', () => {
    expect(cancelEvent('1', '14:00')).toEqual({ type: 'cancel', time: '14:00', request: null, request_id: '1', engineer_id: null });
    expect(unavailableEvent('E01', '14:00')).toMatchObject({ type: 'engineer_unavailable', engineer_id: 'E01', request_id: null });
  });

  it('starts the default urgent window no earlier than the first shift of an available engineer', () => {
    const { engineers } = makePlanningState();
    expect(earliestShiftStart(engineers)).toBe('10:00');
    const withShift = (id: string, shiftStart: string) =>
      engineers.map((engineer) => (engineer.id === id ? { ...engineer, shift_start: shiftStart } : engineer));
    expect(earliestShiftStart(withShift('E03', '08:00'))).toBe('10:00');
    expect(earliestShiftStart(withShift('E02', '09:30'))).toBe('09:30');
    expect(earliestShiftStart([])).toBeNull();
    expect(defaultUrgentWindow('00:00', engineers)).toEqual({ windowStart: '10:00', windowEnd: '12:00' });
    expect(defaultUrgentWindow('13:15', engineers)).toEqual({ windowStart: '13:15', windowEnd: '15:15' });
    expect(defaultUrgentWindow('00:00', [])).toEqual({ windowStart: '00:00', windowEnd: '02:00' });
  });

  it('offers by default a slot the brigade still gets to, never one that is about to end', () => {
    const { engineers } = makePlanningState();
    // В 13:15 до конца слота 12:00–14:00 остаётся 45 минут, а работы идут час: в него уже не успеть.
    expect(defaultUrgentWindow('13:15', engineers, WINDOW_GRID, 60)).toEqual({ windowStart: '14:00', windowEnd: '16:00' });
    expect(defaultUrgentWindow('13:15', engineers, WINDOW_GRID, 20)).toEqual({ windowStart: '12:00', windowEnd: '14:00' });
    // До начала смен окно всё равно начинается со смены: первый слот сетки.
    expect(defaultUrgentWindow('00:00', engineers, WINDOW_GRID, 80)).toEqual({ windowStart: '10:00', windowEnd: '12:00' });
    // Вечером успевать некуда: остаётся последний слот, а не первый.
    expect(defaultUrgentWindow('21:40', engineers, WINDOW_GRID, 60)).toEqual({ windowStart: '20:00', windowEnd: '22:00' });
  });

  it('checks the window of the form against the grid and against the time of the event', () => {
    const urgent = { ...form, windowStart: '12:00', windowEnd: '14:00', time: '11:00' };
    expect(validateUrgentForm(urgent, WINDOW_GRID)).toEqual([]);
    expect(validateUrgentForm({ ...urgent, windowStart: '11:30', windowEnd: '13:30' }, WINDOW_GRID)).toEqual([
      'Выберите окно визита из сетки: 10:00–12:00, 12:00–14:00, 14:00–16:00, 16:00–18:00, 18:00–20:00, 20:00–22:00',
    ]);
    // Слот, который к времени события уже прошёл, сервер отклоняет: диспетчер узнаёт это до запроса.
    expect(validateUrgentForm({ ...urgent, time: '14:00' }, WINDOW_GRID)).toEqual([
      'Окно 12:00–14:00 уже закончилось: выберите слот, который ещё не прошёл',
    ]);
    // Без сетки (старый сервер, офлайн) окно по-прежнему вводится временем, и прошедшее отклоняет сервер.
    expect(validateUrgentForm({ ...urgent, time: '14:00' })).toEqual([]);
  });

  it('treats a window left by «как можно скорее» as a new one and asks for a slot', () => {
    const stored = makeAsapRequest();
    const asapForm = requestEditForm(stored);
    // Окно 13:00–22:00 задал сервер, и клиенту его не называли: снятой галочки мало, нужен слот.
    expect(validateRequestEdit(stored, { ...asapForm, asap: false }, '13:30', WINDOW_GRID)).toEqual([
      'Выберите окно визита из сетки: 10:00–12:00, 12:00–14:00, 14:00–16:00, 16:00–18:00, 18:00–20:00, 20:00–22:00',
    ]);
    expect(
      validateRequestEdit(stored, { ...asapForm, asap: false, windowStart: '14:00', windowEnd: '16:00' }, '13:30', WINDOW_GRID),
    ).toEqual([]);
    // Своё окно заявки данных сеткой не проверяется, пока диспетчер его не трогает.
    const emergency = { ...stored, asap: false, window_start: '00:01', window_end: '23:59' } as ServiceRequest;
    const kept = requestEditForm(emergency);
    expect(validateRequestEdit(emergency, { ...kept, durationMin: 90 }, '13:30', WINDOW_GRID)).toEqual([]);
  });

  it('counts visits left after a time and picks the busiest available engineer', () => {
    const state = makePlanningState();
    expect(visitsFrom(state.plan, 'E01', '14:00')).toBe(2);
    expect(visitsFrom(state.plan, 'E02', '13:00')).toBe(1);
    expect(visitsFrom(state.plan, 'E03', '00:00')).toBe(0);
    expect(busiestEngineerId(state.engineers, state.plan, '13:00')).toBe('E01');
    expect(busiestEngineerId([...state.engineers].reverse(), state.plan, '16:00')).toBe('E01');
    const onlyUnavailableBusy = { ...state.plan, routes: [{ ...state.plan.routes[2], visits: state.plan.routes[0].visits }] };
    expect(busiestEngineerId(state.engineers, onlyUnavailableBusy, '09:00')).toBe('E01');
    expect(busiestEngineerId([], state.plan, '13:00')).toBeNull();
  });

  it('generates readable urgent ids', () => {
    expect(newUrgentId(1789430400000)).toMatch(/^URG-[0-9A-Z]+$/);
  });

  it('describes applied events for the dispatcher', () => {
    const engineers = byId(makePlanningState().engineers);
    const requests = byId(makePlanningState().requests);
    expect(describeEvent(unavailableEvent('E03', '13:00'), engineers, requests)).toBe('Инженер недоступен: Бригада Комарь с 13:00');
    expect(describeEvent(cancelEvent('10135', '09:30'), engineers, requests)).toBe('Отмена заявки 10135 в 09:30');
    const urgent = makePlanningState().events[2].event;
    expect(describeEvent(urgent, engineers, requests)).toBe('Срочная заявка URG-001 в 13:00');
    expect(describeEvent({ ...urgent, request: makeAsapRequest() }, engineers, requests)).toBe('Срочная заявка URG-002 как можно скорее, 13:00');
  });

  it('names an urgent request of the day with the URG- prefix, while the event keeps the raw number', () => {
    const state = makeDataUrgentState();
    const engineers = byId(state.engineers);
    const requests = byId(state.requests);
    const cancel = cancelEvent('50104', '13:30');
    expect(cancel.request_id).toBe('50104');
    expect(describeEvent(cancel, engineers, requests)).toBe('Отмена заявки URG-50104 в 13:30');
    // Возврат интерфейс больше не ставит, но в днях, сохранённых раньше, он есть: шкала подписывает его как прежде.
    const restore: PlanEvent = { ...cancel, type: 'restore' };
    expect(describeEvent(restore, engineers, requests)).toBe('Возврат заявки URG-50104 в 13:30');
    expect(describeEvent(reassignEvent('50104', 'E02', '13:30'), engineers, requests)).toBe(
      'Переназначение заявки URG-50104 → Бригада Белузин с 13:30',
    );
    // Обычная заявка остаётся со своим номером, заявка диспетчера не получает приставку дважды.
    expect(describeEvent(cancelEvent('46393', '13:30'), engineers, requests)).toBe('Отмена заявки 46393 в 13:30');
    expect(describeEvent(cancelEvent('URG-001', '13:30'), engineers, requests)).toBe('Отмена заявки URG-001 в 13:30');
  });

  it('builds a transport change event with the new transport only', () => {
    expect(transportChangeEvent('E01', 'bike', '14:00')).toEqual({
      type: 'engineer_transport_changed',
      time: '14:00',
      request: null,
      request_id: null,
      engineer_id: 'E01',
      transport: 'bike',
    });
    expect(EVENT_LABELS.engineer_transport_changed).toBe('Смена транспорта');
  });

  it('describes a transport change with and without the previous transport', () => {
    const engineers = byId(makePlanningState().engineers);
    const requests = byId(makePlanningState().requests);
    const applied = { ...transportChangeEvent('E01', 'bike', '13:30'), previous_transport: 'car' as const };
    expect(describeEvent(applied, engineers, requests)).toBe('Смена транспорта: Бригада Арташкин, Автомобиль → Велосипед с 13:30');
    expect(describeEvent(transportChangeEvent('E03', 'bike', '14:00'), engineers, requests)).toBe('Смена транспорта: Бригада Комарь на Велосипед с 14:00');
  });

  it('suggests a bike instead of a car and a car instead of anything else', () => {
    expect(defaultNewTransport('car')).toBe('bike');
    expect((['bike', 'public'] as const).map(defaultNewTransport)).toEqual(['car', 'car']);
  });

  it('counts the visits of an engineer from a time that require a car', () => {
    const { plan, requests } = makePlanningState();
    expect(carRequiredVisitsFrom(plan, requests, 'E01', '13:00')).toBe(1);
    expect(carRequiredVisitsFrom(plan, requests, 'E01', '15:10')).toBe(1);
    expect(carRequiredVisitsFrom(plan, requests, 'E01', '15:11')).toBe(0);
    expect(carRequiredVisitsFrom(plan, requests, 'E02', '13:00')).toBe(1);
    expect(carRequiredVisitsFrom(plan, requests, 'E02', '14:00')).toBe(0);
    expect(carRequiredVisitsFrom(plan, requests, 'E03', '00:00')).toBe(0);
    expect(carRequiredVisitsFrom(plan, requests, 'E99', '00:00')).toBe(0);
  });
});

describe('request update', () => {
  const state = makePlanningState();
  const requestOf = (id: string) => state.requests.find((request) => request.id === id) as ServiceRequest;
  // 46393: Шарикоподшипниковская, окно 15:00–17:00, 45 мин, локальные работы, нужен автомобиль.
  const original = requestOf('46393');
  const form = requestEditForm(original);

  it('prefills the edit form from the stored request', () => {
    expect(form).toEqual({
      address: 'Город Москва, ул.Шарикоподшипниковская, д. 14',
      point: null,
      windowStart: '15:00',
      windowEnd: '17:00',
      durationMin: 45,
      skill: 'local',
      priority: 'normal',
      transport: 'car',
      asap: false,
      needsEquipment: false,
    });
    expect(requestEditForm(requestOf('74198')).needsEquipment).toBe(true);
    expect(requestEditForm(requestOf('50104')).transport).toBe('');
    expect(requestEditForm(makeAsapRequest()).asap).toBe(true);
  });

  it('builds a full request with the same id, status and source fields', () => {
    const cancelled = requestOf('10135');
    const changed = {
      ...requestEditForm(cancelled),
      windowStart: '11:00',
      windowEnd: '13:00',
      durationMin: 50,
      skill: 'connection' as const,
      priority: 'urgent' as const,
      transport: 'bike' as const,
    };
    expect(requestUpdateEvent(cancelled, changed, '13:30')).toEqual({
      type: 'request_updated',
      time: '13:30',
      request_id: '10135',
      engineer_id: null,
      request: {
        ...cancelled,
        window_start: '11:00',
        window_end: '13:00',
        duration_min: 50,
        skill: 'connection',
        priority: 'urgent',
        transport_required: 'bike',
      },
    });
    expect(requestUpdateEvent(cancelled, changed, '13:30').request).toMatchObject({
      id: '10135',
      status: 'cancelled',
      district: 'Кузьминки',
      source_type_bk: 'Локальная заявка',
      source_type_hd: 'Нет линка',
    });
    expect(requestUpdateEvent(original, { ...form, transport: '' }, '13:30').request?.transport_required).toBeNull();
  });

  it('keeps the stored coordinates while the location is the same', () => {
    const event = requestUpdateEvent(original, { ...form, address: ` ${original.address}  `, durationMin: 60 }, '13:30');
    expect(event.request).toMatchObject({ address: original.address, lat: 55.7195, lon: 37.68, geocode_precision: 'house' });
  });

  it('asks the server to find a new address typed without a map point', () => {
    const event = requestUpdateEvent(original, { ...form, address: 'Город Москва, ул.Юности, д. 5' }, '13:30');
    expect(event.request).toMatchObject({ address: 'Город Москва, ул.Юности, д. 5', lat: null, lon: null, geocode_precision: 'none' });
  });

  it('sends a point picked on the map', () => {
    const point = { lat: 55.72, lon: 37.69 };
    expect(requestUpdateEvent(original, { ...form, point }, '13:30').request).toMatchObject({
      address: original.address,
      lat: 55.72,
      lon: 37.69,
      geocode_precision: 'house',
    });
    expect(requestUpdateEvent(original, { ...form, address: '', point }, '13:30').request?.address).toBe('Точка на карте 55.72000, 37.69000');
  });

  it('lists every changed field in dispatcher language and in a fixed order', () => {
    const next: ServiceRequest = {
      ...original,
      address: 'Город Москва, ул.Юности, д. 5',
      lat: null,
      lon: null,
      window_start: '16:00',
      window_end: '18:00',
      duration_min: 60,
      skill: 'connection',
      priority: 'urgent',
      transport_required: null,
    };
    expect(requestChanges(original, next)).toEqual([
      'адрес ул.Шарикоподшипниковская, д. 14 → ул.Юности, д. 5',
      'окно 15:00–17:00 → 16:00–18:00',
      'длительность 45 → 60 мин',
      'навык Локальные работы → Работы на подключение и дозаказы',
      'приоритет Обычная → Срочная',
      'транспорт Автомобиль → не требуется',
    ]);
    expect(requestChanges(original, original)).toEqual([]);
    expect(requestChanges(original, { ...original, window_end: '18:00' })).toEqual(['окно 15:00–17:00 → 15:00–18:00']);
    expect(requestChanges({ ...original, transport_required: null }, original)).toEqual(['транспорт не требуется → Автомобиль']);
  });

  it('называет отметку об оборудовании после остальных изменений', () => {
    expect(requestChanges(original, { ...original, needs_equipment: true })).toEqual(['оборудование нужно']);
    const carried = { ...original, needs_equipment: true };
    expect(requestChanges(carried, { ...carried, needs_equipment: false, duration_min: 60 })).toEqual([
      'длительность 45 → 60 мин',
      'оборудование не нужно',
    ]);
    expect(updatedRequest(original, { ...form, needsEquipment: true })).toMatchObject({ needs_equipment: true });
  });

  it('names a new map point when only the coordinates changed', () => {
    expect(requestChanges(original, { ...original, lat: 55.72, lon: 37.69 })).toEqual(['точка на карте']);
    expect(requestChanges(original, { ...original, lat: null, lon: null })).toEqual([]);
  });

  it('names turning «как можно скорее» on and off instead of the window', () => {
    const asap = { ...original, asap: true };
    expect(requestChanges(original, asap)).toEqual(['как можно скорее']);
    expect(requestChanges(original, { ...asap, window_start: '13:30', window_end: '22:00', duration_min: 60 })).toEqual([
      'как можно скорее',
      'длительность 45 → 60 мин',
    ]);
    const stored = makeAsapRequest();
    expect(requestChanges(stored, { ...stored, asap: false, window_start: '15:00', window_end: '17:00' })).toEqual([
      'окно вместо «как можно скорее»',
    ]);
    expect(requestChanges(stored, { ...stored, window_start: '16:00' })).toEqual([]);
  });

  it('sends «как можно скорее» with the stored window and the typed window once it is turned off', () => {
    expect(updatedRequest(original, { ...form, asap: true, windowStart: '', windowEnd: '12:00' })).toEqual({ ...original, asap: true });
    const stored = makeAsapRequest();
    expect(updatedRequest(stored, { ...requestEditForm(stored), asap: false, windowStart: '15:00', windowEnd: '17:00' })).toEqual({
      ...stored,
      asap: false,
      window_start: '15:00',
      window_end: '17:00',
    });
    expect(requestUpdateEvent(original, { ...form, asap: true }, '13:30').request?.asap).toBe(true);
  });

  it('skips the window checks of a request edited as soon as possible', () => {
    expect(validateRequestEdit(original, { ...form, asap: true, windowStart: '', windowEnd: '' }, '13:30')).toEqual([]);
    const stored = makeAsapRequest();
    expect(validateRequestEdit(stored, { ...requestEditForm(stored), windowEnd: '12:00' }, '13:30')).toEqual(['Ничего не изменилось']);
    expect(validateRequestEdit(stored, { ...requestEditForm(stored), asap: false, windowEnd: '12:00' }, '13:30')).toEqual([
      'Конец окна должен быть позже начала',
    ]);
  });

  it('validates the edit like the urgent form and refuses an unchanged request', () => {
    expect(validateRequestEdit(original, form, '13:30')).toEqual(['Ничего не изменилось']);
    expect(validateRequestEdit(original, { ...form, durationMin: 60 }, '13:30')).toEqual([]);
    expect(validateRequestEdit(original, { ...form, durationMin: 60 }, '08:00')).toEqual([]);
    expect(validateRequestEdit(original, form, '12:60')).toEqual(['Укажите время в формате ЧЧ:ММ']);
    expect(validateRequestEdit(original, { ...form, address: ' ', windowEnd: '14:00', durationMin: 0 }, '')).toEqual([
      'Укажите адрес или точку на карте',
      'Конец окна должен быть позже начала',
      'Длительность должна быть больше нуля',
      'Укажите время в формате ЧЧ:ММ',
    ]);
    expect(validateRequestEdit(original, { ...form, windowStart: '' }, '13:30')).toEqual(['Укажите окно визита в формате ЧЧ:ММ']);
  });

  it('treats a pinned visit or a visit that started before the clock as started work, like the server', () => {
    const visits = assignmentIndex(state.plan);
    const visitOf = (id: string) => visits.get(id)?.visit;
    expect(isWorkStarted(requestOf('74198'), visitOf('74198'), '09:00')).toBe(true);
    expect(isWorkStarted(requestOf('50104'), visitOf('50104'), '13:00')).toBe(false);
    // Визит 50104 начинается в 14:00: в 14:00 работа ещё не началась, в 14:01 уже идёт.
    expect(isWorkStarted(requestOf('50104'), visitOf('50104'), '14:00')).toBe(false);
    expect(isWorkStarted(requestOf('50104'), visitOf('50104'), '14:01')).toBe(true);
    expect(isWorkStarted({ ...requestOf('74198'), status: 'cancelled' }, visitOf('74198'), '13:00')).toBe(false);
    expect(isWorkStarted(requestOf('18754'), undefined, '23:00')).toBe(false);
  });

  it('shares one rule for the edit and cancel buttons of a request at the clock, with no restore for a cancelled one', () => {
    const visits = assignmentIndex(state.plan);
    const idle = { busy: false, clock: '13:30' };
    const actionsOf = (id: string, patch = {}) => requestActionState(requestOf(id), visits.get(id)?.visit, { ...idle, ...patch });

    expect(actionsOf('50104')).toEqual({
      cancelled: false,
      disabled: false,
      editTitle: undefined,
      cancelTitle: undefined,
      cancelEvent: cancelEvent('50104', '13:30'),
    });
    // Отменённая заявка помечена: кнопки «Отменить» у неё нет, а «Вернуть» интерфейс больше не предлагает.
    expect(actionsOf('10135')).toMatchObject({ cancelled: true, disabled: false });
    expect(actionsOf('10135')).not.toHaveProperty('cancelLabel');
    expect(actionsOf('50104', { clock: '08:15' }).cancelEvent).toEqual(cancelEvent('50104', '08:15'));
    expect(actionsOf('50104', { clock: '14:30' })).toMatchObject({
      disabled: true,
      editTitle: 'Работа уже началась, изменить нельзя',
      cancelTitle: 'Работа уже началась, отменить нельзя',
    });
    expect(actionsOf('74198')).toMatchObject({
      disabled: true,
      editTitle: 'Работа уже началась, изменить нельзя',
      cancelTitle: 'Работа уже началась, отменить нельзя',
    });
    expect(actionsOf('50104', { busy: true })).toMatchObject({ disabled: true, editTitle: undefined, cancelTitle: undefined });
  });

  it('describes an applied request update with its changes and a client event without them', () => {
    const engineers = byId(state.engineers);
    const requests = byId(state.requests);
    expect(describeEvent(makeRequestUpdateEvent(), engineers, requests)).toBe(
      'Изменена заявка 50104 с 13:30: окно 14:00–16:00 → 15:00–17:00, длительность 45 → 60 мин',
    );
    expect(describeEvent(makeRequestUpdateEvent({ previous_request: null }), engineers, requests)).toBe('Изменена заявка 50104 с 13:30');
    const previous = makeRequestUpdateEvent().previous_request!;
    const asap = makeRequestUpdateEvent({ request: { ...previous, asap: true, window_start: '13:30', window_end: '22:00' } });
    expect(describeEvent(asap, engineers, requests)).toBe('Изменена заявка 50104 с 13:30: как можно скорее');
    expect(EVENT_LABELS.request_updated).toBe('Изменение заявки');
  });
});

describe('engineer delay', () => {
  const state = makePlanningState();
  const engineers = byId(state.engineers);
  const requests = byId(state.requests);
  const late = (requestId: string, lateMin: number) => ({ request_id: requestId, planned_start: '14:00', forecast_start: '16:30', late_min: lateMin });

  it('builds a delay event with the engineer and the minutes', () => {
    expect(delayEvent('E01', 30, '13:30')).toEqual({
      type: 'engineer_delayed',
      time: '13:30',
      request: null,
      request_id: null,
      engineer_id: 'E01',
      delay_min: 30,
    });
    expect(DELAY_PRESETS).toEqual([15, 30, 60]);
    expect(EVENT_LABELS.engineer_delayed).toBe('Задержка инженера');
  });

  it('accepts a whole number of minutes from 5 to 480', () => {
    expect([5, 30, 480].map(validateDelay)).toEqual([null, null, null]);
    for (const value of [4, 481, 0, -15, 12.5, Number.NaN]) {
      expect(validateDelay(value)).toBe('Задержка должна быть от 5 до 480 минут');
    }
  });

  it('describes a delay with the engineer name, the minutes and the time', () => {
    expect(describeEvent(makeDelayEvent(), engineers, requests)).toBe('Задержка: Бригада Арташкин на 150 мин с 13:30');
    expect(describeEvent(delayEvent('E99', 15, '14:00'), engineers, requests)).toBe('Задержка: E99 на 15 мин с 14:00');
  });

  it('forecasts how late the clients would be without replanning', () => {
    expect(forecastLines(makeDelayForecast(), requests, engineers)).toEqual(['Без перепланирования опоздали бы к 2 клиентам на 35–45 мин']);
    const five = ['1', '2', '3', '4', '5'].map((id, index) => late(id, 10 + index * 5));
    expect(forecastLines(makeDelayForecast({ late_without_replan: five }), requests, engineers)).toEqual([
      'Без перепланирования опоздали бы к 5 клиентам на 10–30 мин',
    ]);
  });

  it('names a single lateness value when every client is equally late', () => {
    expect(forecastLines(makeDelayForecast({ late_without_replan: [late('50104', 35)] }), requests, engineers)).toEqual([
      'Без перепланирования опоздали бы к 1 клиенту на 35 мин',
    ]);
    const same = [late('50104', 20), late('46393', 20)];
    expect(forecastLines(makeDelayForecast({ late_without_replan: same }), requests, engineers)).toEqual([
      'Без перепланирования опоздали бы к 2 клиентам на 20 мин',
    ]);
  });

  it('says when the delay would not make anyone late', () => {
    expect(forecastLines(makeDelayForecast({ late_without_replan: [] }), requests, engineers)).toEqual(['Задержка не привела бы к опозданиям']);
  });

  it('adds the overtime the delay would cause', () => {
    expect(forecastLines(makeDelayForecast({ overtime_without_replan_min: 25 }), requests, engineers)).toEqual([
      'Без перепланирования опоздали бы к 2 клиентам на 35–45 мин и переработка 25 мин',
    ]);
    expect(forecastLines(makeDelayForecast({ late_without_replan: [], overtime_without_replan_min: 25 }), requests, engineers)).toEqual([
      'Без перепланирования была бы переработка 25 мин',
    ]);
  });
});

describe('request reassignment', () => {
  const state = makePlanningState();
  const engineers = byId(state.engineers);
  const requests = byId(state.requests);
  const requestOf = (id: string) => state.requests.find((request) => request.id === id) as ServiceRequest;
  const engineerOf = (id: string) => engineers.get(id)!;

  it('builds a reassignment event for the brigade at the time on the clock', () => {
    expect(reassignEvent('50104', 'E02', '13:30')).toEqual({
      type: 'request_reassigned',
      time: '13:30',
      request: null,
      request_id: '50104',
      engineer_id: 'E02',
    });
    expect(EVENT_LABELS.request_reassigned).toBe('Переназначение заявки');
  });

  it('describes a reassignment from the previous brigade and one without it', () => {
    expect(describeEvent(makeReassignEvent(), engineers, requests)).toBe('Переназначение заявки 50104: Бригада Арташкин → Бригада Белузин с 13:30');
    // Заявка была без бригады или событие ещё не применено: прежней бригады нет, имя новой не склоняется.
    expect(describeEvent(makeReassignEvent({ previous_engineer_id: null }), engineers, requests)).toBe('Переназначение заявки 50104 → Бригада Белузин с 13:30');
    expect(describeEvent(reassignEvent('18754', 'E01', '14:00'), engineers, requests)).toBe('Переназначение заявки 18754 → Бригада Арташкин с 14:00');
    expect(describeEvent(makeReassignEvent({ engineer_id: 'E99', previous_engineer_id: 'E98' }), engineers, requests)).toBe(
      'Переназначение заявки 50104: E98 → E99 с 13:30',
    );
  });

  it('links request events to their request and leaves engineer events without one', () => {
    expect(eventRequestId(makeReassignEvent())).toBe('50104');
    expect(eventRequestId(cancelEvent('10135', '09:30'))).toBe('10135');
    expect(eventRequestId(makeRequestUpdateEvent())).toBe('50104');
    expect(eventRequestId(state.events[2].event)).toBe('URG-001');
    expect(eventRequestId(unavailableEvent('E03', '13:00'))).toBeNull();
    expect(eventRequestId(makeDelayEvent())).toBeNull();
  });

  it('names why a brigade cannot take the request, like the server', () => {
    const back = { ...engineerOf('E03'), available: true, unavailable_from: null };
    expect(brigadeIneligibility(requestOf('50104'), engineerOf('E02'))).toBeNull();
    expect(brigadeIneligibility(requestOf('50104'), engineerOf('E03'))).toBe('недоступна с 13:00');
    expect(brigadeIneligibility(requestOf('50104'), { ...engineerOf('E03'), unavailable_from: null })).toBe('недоступна');
    expect(brigadeIneligibility(requestOf('18754'), back)).toBe('нет навыка «Работы на подключение и дозаказы»');
    expect(brigadeIneligibility(requestOf('46393'), back)).toBe('нужен транспорт «Автомобиль»');
    expect(brigadeIneligibility(requestOf('50104'), back)).toBeNull();
  });

  it('lists every brigade with the current one, the reasons and the visits in the current plan', () => {
    expect(brigadeOptions(requestOf('50104'), state.engineers, state.plan).map(({ engineer, ...option }) => ({ id: engineer.id, ...option }))).toEqual([
      { id: 'E01', current: true, disabled: false, note: 'в плане' },
      { id: 'E02', current: false, disabled: false, note: '2 заявки' },
      { id: 'E03', current: false, disabled: true, note: 'недоступна с 13:00' },
    ]);
    const unassigned = brigadeOptions(requestOf('18754'), state.engineers, state.plan);
    expect(unassigned.map((option) => [option.current, option.note])).toEqual([
      [false, '4 заявки'],
      [false, '2 заявки'],
      [false, 'недоступна с 13:00'],
    ]);
    // Бригада текущего плана доступна для выбора, даже если по правилам уже не подошла бы: выбор просто закрывает список.
    const sick = state.engineers.map((engineer) => (engineer.id === 'E02' ? { ...engineer, available: false, unavailable_from: '15:00' } : engineer));
    expect(brigadeOptions(requestOf('84627'), sick, state.plan)[1]).toMatchObject({ current: true, disabled: false, note: 'в плане' });
  });

  it('allows the reassignment by the rule of the request buttons and refuses cancelled requests and requests off the map', () => {
    const visits = assignmentIndex(state.plan);
    const idle = { busy: false, clock: '13:30' };
    const lockOf = (request: ServiceRequest, patch = {}) => reassignState(request, visits.get(request.id)?.visit, { ...idle, ...patch });
    expect(lockOf(requestOf('50104'))).toEqual({ disabled: false, title: undefined });
    expect(lockOf(requestOf('18754'))).toEqual({ disabled: false, title: undefined });
    expect(lockOf(requestOf('74198'))).toEqual({ disabled: true, title: 'Работа уже началась, переназначить нельзя' });
    expect(lockOf(requestOf('50104'), { clock: '14:30' })).toEqual({ disabled: true, title: 'Работа уже началась, переназначить нельзя' });
    expect(lockOf(requestOf('10135'))).toEqual({ disabled: true, title: 'Заявка отменена' });
    expect(lockOf({ ...requestOf('18754'), lat: null, lon: null })).toEqual({
      disabled: true,
      title: 'Адрес не найден на карте, назначить бригаду нельзя',
    });
    expect(lockOf(requestOf('50104'), { busy: true })).toEqual({ disabled: true, title: undefined });
  });
});
