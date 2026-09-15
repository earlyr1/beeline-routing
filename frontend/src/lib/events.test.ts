import { describe, expect, it } from 'vitest';
import { makePlanningState } from '../test/fixtures';
import {
  buildUrgentEvent,
  cancelEvent,
  describeEvent,
  newUrgentId,
  restoreEvent,
  timeError,
  unavailableEvent,
  validateUrgentForm,
  type UrgentForm,
} from './events';
import { byId } from './planView';

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

  it('generates readable urgent ids', () => {
    expect(newUrgentId(1789430400000)).toMatch(/^URG-[0-9A-Z]+$/);
  });

  it('describes applied events for the dispatcher', () => {
    const engineers = byId(makePlanningState().engineers);
    expect(describeEvent(unavailableEvent('E03', '13:00'), engineers)).toBe('Инженер недоступен: Бригада Комарь с 13:00');
    expect(describeEvent(cancelEvent('10135', '09:30'), engineers)).toBe('Отмена заявки 10135 в 09:30');
  });
});
