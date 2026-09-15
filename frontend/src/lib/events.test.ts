import { describe, expect, it } from 'vitest';
import type { ServiceRequest } from '../api/types';
import { makePlanningState, makeRequestUpdateEvent } from '../test/fixtures';
import {
  buildUrgentEvent,
  busiestEngineerId,
  cancelEvent,
  carRequiredVisitsFrom,
  defaultNewTransport,
  defaultUrgentWindow,
  describeEvent,
  earliestShiftStart,
  isWorkStarted,
  newUrgentId,
  requestChanges,
  requestEditForm,
  requestUpdateEvent,
  restoreEvent,
  timeError,
  transportChangeEvent,
  unavailableEvent,
  validateRequestEdit,
  validateUrgentForm,
  visitsFrom,
  type UrgentForm,
} from './events';
import { EVENT_LABELS } from './format';
import { assignmentIndex, byId } from './planView';

const form: UrgentForm = {
  address: 'Город Москва, ул.Ташкентская, д. 16к2',
  point: null,
  windowStart: '13:00',
  windowEnd: '15:00',
  durationMin: 60,
  skill: 'emergency',
  transport: 'car',
  time: '13:00',
};

describe('events', () => {
  it('accepts a valid urgent form', () => {
    expect(validateUrgentForm(form, '13:00')).toEqual([]);
  });

  it('reports every problem in Russian', () => {
    const errors = validateUrgentForm({ ...form, address: ' ', windowEnd: '12:00', durationMin: 0, time: '12:59' }, '13:00');
    expect(errors).toEqual([
      'Укажите адрес или точку на карте',
      'Конец окна должен быть позже начала',
      'Длительность должна быть больше нуля',
      'Время события не может быть раньше 13:00',
    ]);
    expect(timeError('9', '13:00')).toBe('Укажите время в формате ЧЧ:ММ');
  });

  it('builds an urgent event with a full request from a map point', () => {
    const event = buildUrgentEvent({ ...form, address: '', point: { lat: 55.71, lon: 37.8 }, transport: '' }, 'URG-TEST');
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
    });
    expect(event.request?.address).toBe('Точка на карте 55.71000, 37.80000');
  });

  it('builds cancel, restore and unavailability events', () => {
    expect(cancelEvent('1', '14:00')).toEqual({ type: 'cancel', time: '14:00', request: null, request_id: '1', engineer_id: null });
    expect(restoreEvent('1', '14:00').type).toBe('restore');
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
    expect(describeEvent(unavailableEvent('E03', '13:00'), engineers)).toBe('Инженер недоступен: Бригада Комарь с 13:00');
    expect(describeEvent(cancelEvent('10135', '09:30'), engineers)).toBe('Отмена заявки 10135 в 09:30');
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
    const applied = { ...transportChangeEvent('E01', 'bike', '13:30'), previous_transport: 'car' as const };
    expect(describeEvent(applied, engineers)).toBe('Смена транспорта: Бригада Арташкин, Автомобиль → Велосипед с 13:30');
    expect(describeEvent(transportChangeEvent('E03', 'public', '14:00'), engineers)).toBe(
      'Смена транспорта: Бригада Комарь на Общественный транспорт с 14:00',
    );
  });

  it('suggests a bike instead of a car and a car instead of anything else', () => {
    expect(defaultNewTransport('car')).toBe('bike');
    expect((['foot', 'bike', 'public'] as const).map(defaultNewTransport)).toEqual(['car', 'car', 'car']);
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
    });
    expect(requestEditForm(requestOf('50104')).transport).toBe('');
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

  it('names a new map point when only the coordinates changed', () => {
    expect(requestChanges(original, { ...original, lat: 55.72, lon: 37.69 })).toEqual(['точка на карте']);
    expect(requestChanges(original, { ...original, lat: null, lon: null })).toEqual([]);
  });

  it('validates the edit like the urgent form and refuses an unchanged request', () => {
    expect(validateRequestEdit(original, form, '13:30', '13:00')).toEqual(['Ничего не изменилось']);
    expect(validateRequestEdit(original, { ...form, durationMin: 60 }, '13:30', '13:00')).toEqual([]);
    expect(validateRequestEdit(original, form, '12:00', '13:00')).toEqual(['Время события не может быть раньше 13:00']);
    expect(validateRequestEdit(original, { ...form, address: ' ', windowEnd: '14:00', durationMin: 0 }, '12:59', '13:00')).toEqual([
      'Укажите адрес или точку на карте',
      'Конец окна должен быть позже начала',
      'Длительность должна быть больше нуля',
      'Время события не может быть раньше 13:00',
    ]);
    expect(validateRequestEdit(original, { ...form, windowStart: '' }, '13:30', '13:00')).toEqual(['Укажите окно визита в формате ЧЧ:ММ']);
  });

  it('treats a pinned visit of an active request as started work', () => {
    const visits = assignmentIndex(state.plan);
    const visitOf = (id: string) => visits.get(id)?.visit;
    expect(isWorkStarted(requestOf('74198'), visitOf('74198'))).toBe(true);
    expect(isWorkStarted(requestOf('50104'), visitOf('50104'))).toBe(false);
    expect(isWorkStarted({ ...requestOf('74198'), status: 'cancelled' }, visitOf('74198'))).toBe(false);
    expect(isWorkStarted(requestOf('18754'), undefined)).toBe(false);
  });

  it('describes an applied request update with its changes and a client event without them', () => {
    const engineers = byId(state.engineers);
    expect(describeEvent(makeRequestUpdateEvent(), engineers)).toBe(
      'Изменена заявка 50104 с 13:30: окно 14:00–16:00 → 15:00–17:00, длительность 45 → 60 мин',
    );
    expect(describeEvent(makeRequestUpdateEvent({ previous_request: null }), engineers)).toBe('Изменена заявка 50104 с 13:30');
    expect(EVENT_LABELS.request_updated).toBe('Изменение заявки');
  });
});
