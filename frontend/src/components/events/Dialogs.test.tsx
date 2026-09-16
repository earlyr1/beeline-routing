import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, addTimelineEvent: vi.fn(), moveCursor: vi.fn(), postEvent: vi.fn() };
});

import * as api from '../../api/client';
import type { PlanningState, ServiceRequest, Transport } from '../../api/types';
import { BEFORE_SHIFTS_HINT, cancelEvent, unavailableEvent } from '../../lib/events';
import { toMinutes } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';
import { makeAsapRequest, makeAsapState, makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { EngineerDelayDialog } from './EngineerDelayDialog';
import { EngineerUnavailableDialog } from './EngineerUnavailableDialog';
import { EventToolbar } from './EventToolbar';
import { RequestEditDialog } from './RequestEditDialog';
import { TransportChangeDialog } from './TransportChangeDialog';
import { UrgentRequestDialog } from './UrgentRequestDialog';

const valueOf = (label: string) => (screen.getByLabelText(label) as HTMLInputElement).value;
const optionsOf = (label: string) => within(screen.getByLabelText(label)).getAllByRole('option').map((option) => option.textContent);
const ASAP_HINT =
  'Начало с времени события до конца смен. Ожидание до 4 часов без штрафа, дольше небольшой штраф. Если сегодня никто не успевает, заявка останется неназначенной.';
/** Первый элемент стоит в документе раньше второго. */
const isBefore = (first: HTMLElement, second: HTMLElement) =>
  Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);

function withTransport(state: PlanningState, engineerId: string, transport: Transport): PlanningState {
  return {
    ...state,
    engineers: state.engineers.map((engineer) => (engineer.id === engineerId ? { ...engineer, transport } : engineer)),
  };
}

/** План, где у Белузина больше всего визитов после 13:00, а после 16:00 визитов нет ни у кого. */
function withBusyBeluzin(state: PlanningState): PlanningState {
  const [e01, e02, e03] = state.plan.routes;
  const routes = [{ ...e01, visits: e01.visits.slice(0, 2) }, { ...e02, visits: [...e02.visits, ...e01.visits.slice(2)] }, e03];
  return { ...state, plan: { ...state.plan, routes } };
}

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
});

describe('UrgentRequestDialog', () => {
  it('validates the form and submits an urgent request', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    useAppStore.setState({ applyEvent });
    render(<UrgentRequestDialog onClose={onClose} />);
    const submit = screen.getByRole('button', { name: 'Добавить и перепланировать' });

    fireEvent.click(submit);
    expect(await screen.findByText('Укажите адрес или точку на карте')).toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();

    expect(valueOf('Время события')).toBe('13:00');
    expect(screen.getByLabelText('Время события')).not.toHaveAttribute('min');
    fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Ташкентская, д. 16к2' } });
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '' } });
    fireEvent.click(submit);
    expect(await screen.findByText('Укажите время в формате ЧЧ:ММ')).toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '13:15' } });
    fireEvent.click(submit);
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    const event = applyEvent.mock.calls[0][0];
    expect(event).toMatchObject({ type: 'urgent', time: '13:15', request_id: null });
    expect(event.request).toMatchObject({
      address: 'Город Москва, ул.Ташкентская, д. 16к2',
      window_start: '13:15',
      window_end: '15:15',
      duration_min: 60,
      skill: 'emergency',
      transport_required: 'car',
      priority: 'urgent',
      asap: false,
      needs_equipment: false,
    });
  });

  it('отправляет со срочной заявкой отметку «Нужно оборудование»', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    const equipment = screen.getByLabelText('Нужно оборудование');
    expect(equipment).not.toBeChecked();

    fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Ташкентская, д. 16к2' } });
    fireEvent.click(equipment);
    expect(equipment).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить и перепланировать' }));
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({ needs_equipment: true });
  });

  it('starts the default window inside working hours right after planning the day', async () => {
    const state = makePlanningState({ now: '00:00' });
    resetStore({ datasetId: 'd_test', state, clock: '00:00' });
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    expect(valueOf('Время события')).toBe('00:00');
    expect([valueOf('Окно с'), valueOf('Окно до')]).toEqual(['10:00', '12:00']);
    expect(screen.getByText(BEFORE_SHIFTS_HINT)).toHaveClass('muted');

    fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Ташкентская, д. 16к2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Добавить и перепланировать' }));
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());

    const event = applyEvent.mock.calls[0][0];
    expect(event.time).toBe('00:00');
    const start = toMinutes(event.request.window_start);
    const end = toMinutes(event.request.window_end);
    const overlapping = state.engineers.filter(
      (engineer) => engineer.available && start < toMinutes(engineer.shift_end) && end > toMinutes(engineer.shift_start),
    );
    expect(overlapping.map((engineer) => engineer.id)).toEqual(['E01', 'E02']);
  });

  it('moves the default window with the event time until the dispatcher edits it', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState({ now: '00:00' }), clock: '00:00' });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '09:00' } });
    expect([valueOf('Окно с'), valueOf('Окно до')]).toEqual(['10:00', '12:00']);
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '13:30' } });
    expect([valueOf('Окно с'), valueOf('Окно до')]).toEqual(['13:30', '15:30']);

    fireEvent.change(screen.getByLabelText('Окно до'), { target: { value: '17:00' } });
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '14:00' } });
    expect([valueOf('Окно с'), valueOf('Окно до')]).toEqual(['13:30', '17:00']);
  });

  it('uses a point picked on the map and starts picking mode', () => {
    useAppStore.setState({ pickFor: 'urgent', pickedPoint: { lat: 55.71234, lon: 37.80123 } });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    expect(screen.getByText('Точка: 55.71234, 37.80123')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Указать точку на карте' }));
    expect(useAppStore.getState()).toMatchObject({ pickMode: true, pickFor: 'urgent' });
    expect(screen.getByRole('button', { name: 'Кликните по карте…' })).toBeDisabled();
  });
  it('shows the address search for a map point and fills the empty address with the address found', () => {
    resetStore({
      datasetId: 'd_test',
      state: makePlanningState(),
      clock: '13:00',
      pickFor: 'urgent',
      pickedPoint: { lat: 55.71234, lon: 37.80123 },
      urgentAddressLookup: 'loading',
    });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    expect(screen.getByText('Ищем адрес…')).toHaveClass('muted');
    expect(screen.getByText('Точка: 55.71234, 37.80123')).toBeInTheDocument();
    expect(valueOf('Адрес')).toBe('');

    act(() => useAppStore.setState({ urgentAddressLookup: 'done', urgentSuggestedAddress: 'Москва, Перовская улица, 42к1' }));
    expect(screen.queryByText('Ищем адрес…')).not.toBeInTheDocument();
    expect(valueOf('Адрес')).toBe('Москва, Перовская улица, 42к1');

    // Адрес следующей точки заменяет найденный прежде, пока диспетчер его не правил.
    act(() => useAppStore.setState({ pickedPoint: { lat: 55.76, lon: 37.62 }, urgentAddressLookup: 'loading', urgentSuggestedAddress: null }));
    expect(valueOf('Адрес')).toBe('');
    act(() => useAppStore.setState({ urgentAddressLookup: 'done', urgentSuggestedAddress: 'Москва, Тверская улица, 7' }));
    expect(valueOf('Адрес')).toBe('Москва, Тверская улица, 7');
  });

  it('never replaces an address the dispatcher typed with the address found for the point', () => {
    resetStore({
      datasetId: 'd_test',
      state: makePlanningState(),
      clock: '13:00',
      pickFor: 'urgent',
      pickedPoint: { lat: 55.71234, lon: 37.80123 },
      urgentAddressLookup: 'loading',
    });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Юности, д. 5' } });
    act(() => useAppStore.setState({ urgentAddressLookup: 'done', urgentSuggestedAddress: 'Москва, Перовская улица, 42к1' }));
    expect(valueOf('Адрес')).toBe('Город Москва, ул.Юности, д. 5');
    act(() => useAppStore.setState({ urgentAddressLookup: 'loading', urgentSuggestedAddress: null }));
    expect(valueOf('Адрес')).toBe('Город Москва, ул.Юности, д. 5');
  });

  it('keeps the address empty when nothing was found for the point and sends the point instead', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    resetStore({
      datasetId: 'd_test',
      state: makePlanningState(),
      clock: '13:00',
      applyEvent,
      pickFor: 'urgent',
      pickedPoint: { lat: 55.71234, lon: 37.80123 },
      urgentAddressLookup: 'done',
      urgentSuggestedAddress: null,
    });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    expect(valueOf('Адрес')).toBe('');
    expect(screen.queryByText('Ищем адрес…')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить и перепланировать' }));
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({
      address: 'Точка на карте 55.71234, 37.80123',
      lat: 55.71234,
      lon: 37.80123,
    });
  });
});

describe('UrgentRequestDialog as soon as possible', () => {
  const submit = () => screen.getByRole('button', { name: 'Добавить и перепланировать' });

  it('hides the window behind «Как можно скорее», keeps the typed window and sends the time from the event to the latest shift end', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    const state = makePlanningState();
    // Белузин работает до 23:00, дольше всех доступных; смена недоступного Комаря не считается.
    const engineers = state.engineers.map((engineer) =>
      engineer.id === 'E02' ? { ...engineer, shift_end: '23:00' } : engineer.id === 'E03' ? { ...engineer, shift_end: '23:30' } : engineer,
    );
    resetStore({ datasetId: 'd_test', state: { ...state, engineers }, clock: '13:00', applyEvent });
    render(<UrgentRequestDialog onClose={onClose} />);
    const asap = screen.getByLabelText('Как можно скорее');
    expect(asap).not.toBeChecked();
    expect(isBefore(asap, screen.getByLabelText('Окно с'))).toBe(true);
    expect(screen.queryByText(ASAP_HINT)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Окно до'), { target: { value: '12:00' } });
    fireEvent.click(asap);
    expect(asap).toBeChecked();
    expect(screen.queryByLabelText('Окно с')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Окно до')).not.toBeInTheDocument();
    expect(screen.getByText(ASAP_HINT)).toHaveClass('muted');
    expect(screen.getByLabelText('Длительность, мин')).toBeInTheDocument();

    fireEvent.click(asap);
    expect(asap).not.toBeChecked();
    expect([valueOf('Окно с'), valueOf('Окно до')]).toEqual(['13:00', '12:00']);
    expect(screen.queryByText(ASAP_HINT)).not.toBeInTheDocument();

    fireEvent.click(asap);
    fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Ташкентская, д. 16к2' } });
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '13:15' } });
    fireEvent.click(submit());
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    const event = applyEvent.mock.calls[0][0];
    expect(event).toMatchObject({ type: 'urgent', time: '13:15', request_id: null });
    expect(event.request).toMatchObject({ asap: true, window_start: '13:15', window_end: '23:00', priority: 'urgent', duration_min: 60 });
  });

  it('keeps the map point and the address found for it with «Как можно скорее»', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    resetStore({
      datasetId: 'd_test',
      state: makePlanningState(),
      clock: '13:00',
      applyEvent,
      pickFor: 'urgent',
      pickedPoint: { lat: 55.71234, lon: 37.80123 },
      urgentAddressLookup: 'loading',
    });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    fireEvent.click(screen.getByLabelText('Как можно скорее'));
    expect(screen.getByText('Ищем адрес…')).toBeInTheDocument();
    act(() => useAppStore.setState({ urgentAddressLookup: 'done', urgentSuggestedAddress: 'Москва, Перовская улица, 42к1' }));
    expect(valueOf('Адрес')).toBe('Москва, Перовская улица, 42к1');
    expect(screen.getByText('Точка: 55.71234, 37.80123')).toBeInTheDocument();
    fireEvent.click(submit());
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({
      address: 'Москва, Перовская улица, 42к1',
      lat: 55.71234,
      lon: 37.80123,
      asap: true,
      window_start: '13:00',
      window_end: '22:00',
    });
  });
});

describe('UrgentRequestDialog address of a point picked again', () => {
  const menuPoint = { lat: 55.71234, lon: 37.80123 };

  beforeEach(() => {
    resetStore({
      datasetId: 'd_test',
      state: makePlanningState(),
      clock: '13:00',
      pickFor: 'urgent',
      pickedPoint: menuPoint,
      urgentAddressLookup: 'done',
      urgentSuggestedAddress: 'Москва, Перовская улица, 42к1',
    });
  });

  it('drops the address found for the menu point once the dispatcher picks another point', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    expect(valueOf('Адрес')).toBe('Москва, Перовская улица, 42к1');

    fireEvent.click(screen.getByRole('button', { name: 'Указать точку на карте' }));
    // Пока новая точка не выбрана, прежняя точка и её адрес остаются вместе.
    expect(valueOf('Адрес')).toBe('Москва, Перовская улица, 42к1');
    expect(screen.getByText('Точка: 55.71234, 37.80123')).toBeInTheDocument();

    act(() => useAppStore.getState().finishPick({ lat: 55.76, lon: 37.62 }));
    expect(screen.getByText('Точка: 55.76000, 37.62000')).toBeInTheDocument();
    expect(valueOf('Адрес')).toBe('');

    fireEvent.click(screen.getByRole('button', { name: 'Добавить и перепланировать' }));
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({ address: 'Точка на карте 55.76000, 37.62000', lat: 55.76, lon: 37.62 });
  });

  it('hides the address search when the dispatcher picks another point before the answer', () => {
    useAppStore.setState({ urgentAddressLookup: 'loading', urgentSuggestedAddress: null });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    expect(screen.getByText('Ищем адрес…')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Указать точку на карте' }));
    expect(screen.queryByText('Ищем адрес…')).not.toBeInTheDocument();
    expect(valueOf('Адрес')).toBe('');
  });

  it('keeps an address the dispatcher corrected by hand when another point is picked', () => {
    render(<UrgentRequestDialog onClose={() => undefined} />);
    fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Москва, Перовская улица, 42к2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Указать точку на карте' }));
    act(() => useAppStore.getState().finishPick({ lat: 55.76, lon: 37.62 }));
    expect(valueOf('Адрес')).toBe('Москва, Перовская улица, 42к2');
  });
});

describe('map point ownership', () => {
  const noop = () => undefined;
  const dialog = (name: string) => within(screen.getByRole('dialog', { name }));

  beforeEach(() => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:30', editingRequestId: '46393' });
  });

  it('keeps the points of the urgent request and the request edit apart while both dialogs are open', () => {
    render(
      <>
        <UrgentRequestDialog onClose={noop} />
        <RequestEditDialog />
      </>,
    );
    fireEvent.click(dialog('Срочная заявка').getByRole('button', { name: 'Указать точку на карте' }));
    expect(dialog('Изменить заявку').getByRole('button', { name: 'Указать точку на карте' })).toBeInTheDocument();
    act(() => useAppStore.getState().finishPick({ lat: 55.71234, lon: 37.80123 }));
    expect(dialog('Срочная заявка').getByText('Точка: 55.71234, 37.80123')).toBeInTheDocument();
    expect(dialog('Изменить заявку').queryByText(/Точка:/)).not.toBeInTheDocument();
    expect(dialog('Изменить заявку').queryByText(/Изменится/)).not.toBeInTheDocument();

    fireEvent.click(dialog('Изменить заявку').getByRole('button', { name: 'Указать точку на карте' }));
    act(() => useAppStore.getState().finishPick({ lat: 55.72, lon: 37.69 }));
    expect(dialog('Изменить заявку').getByText('Точка: 55.72000, 37.69000')).toBeInTheDocument();
    expect(dialog('Изменить заявку').getByText('Изменится: точка на карте')).toBeInTheDocument();
    expect(dialog('Срочная заявка').getByText('Точка: 55.71234, 37.80123')).toBeInTheDocument();
    expect(dialog('Срочная заявка').queryByText('Точка: 55.72000, 37.69000')).not.toBeInTheDocument();
  });

  it('does not hand a point picked for the request edit to an urgent request, and closing that dialog keeps it', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    useAppStore.setState({ applyEvent });
    render(<RequestEditDialog />);
    fireEvent.click(screen.getByRole('button', { name: 'Указать точку на карте' }));
    act(() => useAppStore.getState().finishPick({ lat: 55.72, lon: 37.69 }));

    const urgent = render(<UrgentRequestDialog onClose={onClose} />);
    const form = within(urgent.container);
    expect(form.queryByText(/Точка:/)).not.toBeInTheDocument();
    fireEvent.change(form.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Ташкентская, д. 16к2' } });
    fireEvent.click(form.getByRole('button', { name: 'Добавить и перепланировать' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({ lat: null, lon: null, geocode_precision: 'none' });
    expect(useAppStore.getState()).toMatchObject({ pickFor: 'edit', pickedPoint: { lat: 55.72, lon: 37.69 } });
    expect(dialog('Изменить заявку').getByText('Точка: 55.72000, 37.69000')).toBeInTheDocument();
  });
});

describe('EngineerDelayDialog', () => {
  const submitButton = () => screen.getByRole('button', { name: 'Перепланировать' });

  beforeEach(() => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '14:00', delayDialogOpen: true, delayEngineerId: 'E01' });
  });

  it('opens for the engineer of the brigade page, sets the minutes from presets and submits the delay', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<EngineerDelayDialog />);
    expect(screen.getByRole('dialog', { name: 'Задержка инженера' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Задержка инженера' })).toBeInTheDocument();
    expect(optionsOf('Инженер')).toEqual(['Бригада Арташкин (визитов после 14:00: 2)', 'Бригада Белузин (визитов после 14:00: 0)']);
    expect(valueOf('Инженер')).toBe('E01');
    expect([valueOf('На сколько минут'), valueOf('Задержка с')]).toEqual(['30', '14:00']);
    expect(screen.getByText('Если инженер не успевает к клиентам, их заявки перейдут другим')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '30 мин' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: '60 мин' }));
    expect(valueOf('На сколько минут')).toBe('60');
    expect(screen.getByRole('button', { name: '60 мин' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '30 мин' })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.change(screen.getByLabelText('На сколько минут'), { target: { value: '45' } });
    expect(['15 мин', '30 мин', '60 мин'].map((name) => screen.getByRole('button', { name }).getAttribute('aria-pressed'))).toEqual([
      'false',
      'false',
      'false',
    ]);
    fireEvent.click(screen.getByRole('button', { name: '15 мин' }));
    expect(valueOf('На сколько минут')).toBe('15');

    fireEvent.click(submitButton());
    await waitFor(() => expect(useAppStore.getState().delayDialogOpen).toBe(false));
    expect(applyEvent).toHaveBeenCalledWith({
      type: 'engineer_delayed',
      time: '14:00',
      request: null,
      request_id: null,
      engineer_id: 'E01',
      delay_min: 15,
    });
  });

  it('preselects the engineer of the brigade page and keeps it when the time changes', () => {
    resetStore({ datasetId: 'd_test', state: withBusyBeluzin(makePlanningState()), clock: '13:00', delayDialogOpen: true, delayEngineerId: 'E01' });
    render(<EngineerDelayDialog />);
    expect(valueOf('Инженер')).toBe('E01');
    fireEvent.change(screen.getByLabelText('Задержка с'), { target: { value: '16:00' } });
    expect(valueOf('Инженер')).toBe('E01');
  });

  it('falls back to the busiest engineer for a brigade that is already unavailable and follows the time until the dispatcher picks one', () => {
    resetStore({ datasetId: 'd_test', state: withBusyBeluzin(makePlanningState()), clock: '13:00', delayDialogOpen: true, delayEngineerId: 'E03' });
    render(<EngineerDelayDialog />);
    expect(valueOf('Инженер')).toBe('E02');
    expect(screen.getByRole('option', { name: 'Бригада Белузин (визитов после 13:00: 3)' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Задержка с'), { target: { value: '16:00' } });
    expect(valueOf('Инженер')).toBe('E01');

    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E02' } });
    fireEvent.change(screen.getByLabelText('Задержка с'), { target: { value: '13:30' } });
    expect(valueOf('Инженер')).toBe('E02');
  });

  it('checks the minutes and the time before replanning', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<EngineerDelayDialog />);
    const minutes = screen.getByLabelText('На сколько минут');
    for (const value of ['4', '481', '']) {
      fireEvent.change(minutes, { target: { value } });
      fireEvent.click(submitButton());
      expect(await screen.findByText('Задержка должна быть от 5 до 480 минут')).toBeInTheDocument();
    }
    fireEvent.change(minutes, { target: { value: '480' } });
    fireEvent.change(screen.getByLabelText('Задержка с'), { target: { value: '' } });
    fireEvent.click(submitButton());
    expect(await screen.findByText('Укажите время в формате ЧЧ:ММ')).toBeInTheDocument();
    expect(screen.queryByText('Задержка должна быть от 5 до 480 минут')).not.toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();

    // Время раньше часов принимается: событие встанет на шкалу дня в прошлое, и план пересчитается с него.
    fireEvent.change(screen.getByLabelText('Задержка с'), { target: { value: '12:00' } });
    fireEvent.click(submitButton());
    await waitFor(() => expect(applyEvent).toHaveBeenCalledWith(expect.objectContaining({ time: '12:00', delay_min: 480 })));
  });

  it('stays open when the server rejects the delay and closes on «Отмена»', async () => {
    const applyEvent = vi.fn().mockResolvedValue(false);
    useAppStore.setState({ applyEvent, delayEngineerId: 'E02' });
    render(<EngineerDelayDialog />);
    fireEvent.click(submitButton());
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0]).toMatchObject({ engineer_id: 'E02', delay_min: 30 });
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: true, delayEngineerId: 'E02' });
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: false, delayEngineerId: null });
  });
});

describe('EngineerUnavailableDialog', () => {
  beforeEach(() => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '14:00', engineerDialog: { kind: 'unavailable', engineerId: 'E02' } });
  });

  it('opens as a floating dialog for the engineer of the brigade page and submits the event', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<EngineerUnavailableDialog />);
    expect(screen.getByRole('dialog', { name: 'Инженер недоступен' })).toHaveClass('dialog', 'dialog--floating');
    const options = screen.getAllByRole('option').map((option) => option.textContent);
    expect(options).toEqual(['Бригада Арташкин (визитов после 14:00: 2)', 'Бригада Белузин (визитов после 14:00: 0)']);
    expect(valueOf('Инженер')).toBe('E02');
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    await waitFor(() => expect(useAppStore.getState().engineerDialog).toBeNull());
    expect(applyEvent).toHaveBeenCalledWith({
      type: 'engineer_unavailable',
      time: '14:00',
      request: null,
      request_id: null,
      engineer_id: 'E02',
    });
  });

  it('keeps the engineer of the brigade page when the time changes and lets the dispatcher pick another one', () => {
    resetStore({
      datasetId: 'd_test',
      state: withBusyBeluzin(makePlanningState()),
      clock: '13:00',
      engineerDialog: { kind: 'unavailable', engineerId: 'E01' },
    });
    render(<EngineerUnavailableDialog />);
    expect(valueOf('Инженер')).toBe('E01');
    fireEvent.change(screen.getByLabelText('Недоступен с'), { target: { value: '16:00' } });
    expect(screen.getByRole('option', { name: 'Бригада Белузин (визитов после 16:00: 0)' })).toBeInTheDocument();
    expect(valueOf('Инженер')).toBe('E01');
    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E02' } });
    expect(valueOf('Инженер')).toBe('E02');
  });

  it('falls back to the engineer with the most visits after the time when the brigade is already unavailable', () => {
    resetStore({
      datasetId: 'd_test',
      state: withBusyBeluzin(makePlanningState()),
      clock: '13:00',
      engineerDialog: { kind: 'unavailable', engineerId: 'E03' },
    });
    render(<EngineerUnavailableDialog />);
    expect(valueOf('Инженер')).toBe('E02');
    expect(screen.getByRole('option', { name: 'Бригада Белузин (визитов после 13:00: 3)' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Недоступен с'), { target: { value: '16:00' } });
    expect(valueOf('Инженер')).toBe('E01');
  });

  it('closes on «Отмена» and stays hidden while the other engineer dialog is open', () => {
    const view = render(<EngineerUnavailableDialog />);
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(useAppStore.getState().engineerDialog).toBeNull();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    view.unmount();
    useAppStore.setState({ engineerDialog: { kind: 'transport', engineerId: 'E02' } });
    expect(render(<EngineerUnavailableDialog />).container).toBeEmptyDOMElement();
  });
});

describe('event dialogs on the clock of the day', () => {
  beforeEach(() => {
    vi.mocked(api.addTimelineEvent).mockReset();
    vi.mocked(api.moveCursor).mockReset();
    vi.mocked(api.postEvent).mockReset();
  });

  it('preselects the busiest engineer at the clock when an engineer dialog opens without an engineer', () => {
    const state = withBusyBeluzin(makePlanningState());
    resetStore({ datasetId: 'd_test', state, clock: '13:00', delayDialogOpen: true, delayEngineerId: null });
    const delay = render(<EngineerDelayDialog />);
    expect([valueOf('Инженер'), valueOf('Задержка с')]).toEqual(['E02', '13:00']);
    fireEvent.change(screen.getByLabelText('Задержка с'), { target: { value: '16:00' } });
    expect(valueOf('Инженер')).toBe('E01');
    delay.unmount();

    resetStore({ datasetId: 'd_test', state, clock: '13:00', engineerDialog: { kind: 'unavailable', engineerId: null } });
    const unavailable = render(<EngineerUnavailableDialog />);
    expect([valueOf('Инженер'), valueOf('Недоступен с')]).toEqual(['E02', '13:00']);
    fireEvent.change(screen.getByLabelText('Недоступен с'), { target: { value: '16:00' } });
    expect(valueOf('Инженер')).toBe('E01');
    unavailable.unmount();

    resetStore({ datasetId: 'd_test', state, clock: '13:00', engineerDialog: { kind: 'transport', engineerId: null } });
    render(<TransportChangeDialog />);
    expect([valueOf('Инженер'), valueOf('Сменить с')]).toEqual(['E02', '13:00']);
    fireEvent.change(screen.getByLabelText('Сменить с'), { target: { value: '16:00' } });
    expect(valueOf('Инженер')).toBe('E01');
  });

  it('renders the delay dialog only while it is open', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00', delayDialogOpen: false, delayEngineerId: 'E01' });
    expect(render(<EngineerDelayDialog />).container).toBeEmptyDOMElement();
  });

  it('warns in every event dialog that an event before the shifts replans the whole day', () => {
    const early = { datasetId: 'd_test', state: makePlanningState(), clock: '08:30' };
    const dialogs = [
      { patch: {}, element: <UrgentRequestDialog onClose={() => undefined} />, time: 'Время события' },
      { patch: { delayDialogOpen: true }, element: <EngineerDelayDialog />, time: 'Задержка с' },
      { patch: { engineerDialog: { kind: 'unavailable' as const, engineerId: null } }, element: <EngineerUnavailableDialog />, time: 'Недоступен с' },
      { patch: { engineerDialog: { kind: 'transport' as const, engineerId: null } }, element: <TransportChangeDialog />, time: 'Сменить с' },
      { patch: { editingRequestId: '46393' }, element: <RequestEditDialog />, time: 'Время события' },
    ];
    for (const { patch, element, time } of dialogs) {
      resetStore({ ...early, ...patch });
      const view = render(element);
      expect(valueOf(time)).toBe('08:30');
      expect(screen.getByText(BEFORE_SHIFTS_HINT)).toHaveClass('muted');
      fireEvent.change(screen.getByLabelText(time), { target: { value: '10:00' } });
      expect(screen.queryByText(BEFORE_SHIFTS_HINT)).not.toBeInTheDocument();
      view.unmount();
    }
  });

  it('posts the event to the timeline of the day at the clock time and keeps the dialog open on a refusal', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00', engineerDialog: { kind: 'unavailable', engineerId: 'E02' } });
    vi.mocked(api.addTimelineEvent)
      .mockRejectedValueOnce(new api.ApiError(422, 'Инженер E02 уже недоступен.'))
      .mockResolvedValueOnce(makePlanningState({ version: 5 }));
    render(<EngineerUnavailableDialog />);
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    await waitFor(() => expect(useAppStore.getState().error).toBe('Инженер E02 уже недоступен.'));
    expect(useAppStore.getState()).toMatchObject({ engineerDialog: { kind: 'unavailable', engineerId: 'E02' }, busy: false });

    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    await waitFor(() => expect(useAppStore.getState().engineerDialog).toBeNull());
    expect(vi.mocked(api.addTimelineEvent).mock.calls).toEqual([
      ['d_test', unavailableEvent('E02', '13:00')],
      ['d_test', unavailableEvent('E02', '13:00')],
    ]);
    expect(useAppStore.getState().state?.version).toBe(5);
    expect(api.postEvent).not.toHaveBeenCalled();
    expect(api.moveCursor).not.toHaveBeenCalled();
  });

  it('sends an event earlier than the clock to the timeline as it is, without moving the plan', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
    vi.mocked(api.addTimelineEvent).mockResolvedValue(makePlanningState({ version: 5 }));
    expect(await useAppStore.getState().applyEvent(cancelEvent('46393', '11:30'))).toBe(true);
    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', cancelEvent('46393', '11:30'));
    expect(api.postEvent).not.toHaveBeenCalled();
  });
});

describe('TransportChangeDialog', () => {
  beforeEach(() => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00', engineerDialog: { kind: 'transport', engineerId: 'E01' } });
  });

  it('opens as a floating dialog for the engineer of the brigade page, suggests a bike instead of the car and submits', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent, clock: '14:00' });
    render(<TransportChangeDialog />);
    expect(screen.getByRole('dialog', { name: 'Смена транспорта' })).toHaveClass('dialog', 'dialog--floating');
    expect(optionsOf('Инженер')).toEqual([
      'Бригада Арташкин · Автомобиль (визитов после 14:00: 2)',
      'Бригада Белузин · Автомобиль (визитов после 14:00: 0)',
    ]);
    expect(valueOf('Инженер')).toBe('E01');
    expect(valueOf('Сменить с')).toBe('14:00');
    expect(optionsOf('Новый транспорт')).toEqual(['Велосипед', 'Общественный транспорт и пешком']);
    expect(valueOf('Новый транспорт')).toBe('bike');
    expect(screen.getByText('Заявок с требованием «Автомобиль» после 14:00: 1, их перераспределит оптимизатор')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Новый транспорт'), { target: { value: 'public' } });
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    await waitFor(() => expect(useAppStore.getState().engineerDialog).toBeNull());
    expect(applyEvent).toHaveBeenCalledWith({
      type: 'engineer_transport_changed',
      time: '14:00',
      request: null,
      request_id: null,
      engineer_id: 'E01',
      transport: 'public',
    });
  });

  it('hides the car hint when no car-only requests are left after the time', () => {
    useAppStore.setState({ clock: '16:00' });
    render(<TransportChangeDialog />);
    expect(valueOf('Новый транспорт')).toBe('bike');
    expect(screen.queryByText(/Заявок с требованием/)).not.toBeInTheDocument();
  });

  it('resets the new transport to the default of the chosen engineer until the dispatcher picks one', () => {
    resetStore({
      datasetId: 'd_test',
      state: withTransport(makePlanningState(), 'E02', 'bike'),
      clock: '13:00',
      engineerDialog: { kind: 'transport', engineerId: 'E01' },
    });
    render(<TransportChangeDialog />);
    expect(valueOf('Инженер')).toBe('E01');
    expect(valueOf('Новый транспорт')).toBe('bike');

    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E02' } });
    expect(valueOf('Новый транспорт')).toBe('car');
    expect(optionsOf('Новый транспорт')).toEqual(['Автомобиль', 'Общественный транспорт и пешком']);
    expect(screen.getByText('Инженер сможет брать заявки, которым нужен автомобиль')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E01' } });
    expect(valueOf('Новый транспорт')).toBe('bike');
    expect(screen.queryByText('Инженер сможет брать заявки, которым нужен автомобиль')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Новый транспорт'), { target: { value: 'public' } });
    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E02' } });
    expect(valueOf('Новый транспорт')).toBe('public');
  });

  it('keeps the engineer of the brigade page and its transport when the time changes', () => {
    const state = withTransport(withBusyBeluzin(makePlanningState()), 'E02', 'public');
    resetStore({ datasetId: 'd_test', state, clock: '13:00', engineerDialog: { kind: 'transport', engineerId: 'E01' } });
    render(<TransportChangeDialog />);
    expect(valueOf('Инженер')).toBe('E01');
    expect(valueOf('Новый транспорт')).toBe('bike');
    fireEvent.change(screen.getByLabelText('Сменить с'), { target: { value: '16:00' } });
    expect(valueOf('Инженер')).toBe('E01');
    expect(valueOf('Новый транспорт')).toBe('bike');
  });

  it('falls back to the busiest available engineer when the brigade is already unavailable', () => {
    const state = withTransport(withBusyBeluzin(makePlanningState()), 'E02', 'public');
    resetStore({ datasetId: 'd_test', state, clock: '13:00', engineerDialog: { kind: 'transport', engineerId: 'E03' } });
    render(<TransportChangeDialog />);
    expect(valueOf('Инженер')).toBe('E02');
    expect(screen.getByRole('option', { name: 'Бригада Белузин · Общественный транспорт и пешком (визитов после 13:00: 3)' })).toBeInTheDocument();
    expect(valueOf('Новый транспорт')).toBe('car');

    fireEvent.change(screen.getByLabelText('Сменить с'), { target: { value: '16:00' } });
    expect(valueOf('Инженер')).toBe('E01');
    expect(valueOf('Новый транспорт')).toBe('bike');
  });

  it('accepts a time earlier than the clock and rejects only a time in a wrong format', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<TransportChangeDialog />);
    expect(valueOf('Сменить с')).toBe('13:00');
    fireEvent.change(screen.getByLabelText('Сменить с'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    expect(await screen.findByText('Укажите время в формате ЧЧ:ММ')).toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Сменить с'), { target: { value: '12:00' } });
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    await waitFor(() => expect(applyEvent).toHaveBeenCalledWith(expect.objectContaining({ time: '12:00', engineer_id: 'E01' })));
  });

  it('stays open when the server rejects the change and closes on «Отмена»', async () => {
    const applyEvent = vi.fn().mockResolvedValue(false);
    useAppStore.setState({ applyEvent });
    render(<TransportChangeDialog />);
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(useAppStore.getState().engineerDialog).toEqual({ kind: 'transport', engineerId: 'E01' });
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(useAppStore.getState().engineerDialog).toBeNull();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('RequestEditDialog', () => {
  // 46393: Шарикоподшипниковская, окно 15:00–17:00, 45 мин, локальные работы, нужен автомобиль.
  const original = makePlanningState().requests.find((request) => request.id === '46393') as ServiceRequest;
  const submitButton = () => screen.getByRole('button', { name: 'Сохранить и перепланировать' });

  beforeEach(() => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:30', editingRequestId: '46393' });
  });

  it('prefills the form from the current request', () => {
    render(<RequestEditDialog />);
    expect(screen.getByRole('dialog', { name: 'Изменить заявку' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Изменить заявку 46393' })).toBeInTheDocument();
    expect(valueOf('Адрес')).toBe('Город Москва, ул.Шарикоподшипниковская, д. 14');
    expect([valueOf('Окно с'), valueOf('Окно до'), valueOf('Длительность, мин')]).toEqual(['15:00', '17:00', '45']);
    expect([valueOf('Навык'), valueOf('Приоритет'), valueOf('Транспорт'), valueOf('Время события')]).toEqual([
      'local',
      'normal',
      'car',
      '13:30',
    ]);
    expect(optionsOf('Приоритет')).toEqual(['Обычная', 'Срочная']);
    expect(optionsOf('Транспорт')).toEqual(['Не требуется', 'Автомобиль', 'Велосипед', 'Общественный транспорт и пешком']);
    expect(screen.queryByText(/Изменится/)).not.toBeInTheDocument();
  });

  it('defaults the event time to the clock even when the plan is at a later time', () => {
    useAppStore.setState({ clock: '12:00' });
    render(<RequestEditDialog />);
    expect(valueOf('Время события')).toBe('12:00');
    expect(screen.getByLabelText('Время события')).not.toHaveAttribute('min');
  });

  it('refuses to submit an unchanged request', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<RequestEditDialog />);
    fireEvent.click(submitButton());
    expect(await screen.findByText('Ничего не изменилось')).toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();
  });

  it('checks the window and the event time', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<RequestEditDialog />);
    fireEvent.change(screen.getByLabelText('Окно до'), { target: { value: '14:30' } });
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '' } });
    fireEvent.click(submitButton());
    expect(await screen.findByText('Конец окна должен быть позже начала')).toBeInTheDocument();
    expect(screen.getByText('Укажите время в формате ЧЧ:ММ')).toBeInTheDocument();
    expect(screen.queryByText('Ничего не изменилось')).not.toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();
  });

  it('previews the changes while editing', () => {
    render(<RequestEditDialog />);
    fireEvent.change(screen.getByLabelText('Длительность, мин'), { target: { value: '60' } });
    fireEvent.change(screen.getByLabelText('Приоритет'), { target: { value: 'urgent' } });
    expect(screen.getByText('Изменится: длительность 45 → 60 мин, приоритет Обычная → Срочная')).toBeInTheDocument();
  });

  it('sends a new address without coordinates so the server finds it on the map', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<RequestEditDialog />);
    fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Юности, д. 5' } });
    expect(screen.getByText('Изменится: адрес ул.Шарикоподшипниковская, д. 14 → ул.Юности, д. 5')).toBeInTheDocument();
    fireEvent.click(submitButton());
    await waitFor(() => expect(useAppStore.getState().editingRequestId).toBeNull());
    expect(applyEvent).toHaveBeenCalledWith({
      type: 'request_updated',
      time: '13:30',
      request_id: '46393',
      engineer_id: null,
      request: { ...original, address: 'Город Москва, ул.Юности, д. 5', lat: null, lon: null, geocode_precision: 'none' },
    });
  });

  it('keeps the original coordinates when the location did not change', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<RequestEditDialog />);
    fireEvent.change(screen.getByLabelText('Окно до'), { target: { value: '18:00' } });
    fireEvent.change(screen.getByLabelText('Навык'), { target: { value: 'connection' } });
    fireEvent.change(screen.getByLabelText('Транспорт'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '14:00' } });
    fireEvent.click(submitButton());
    await waitFor(() => expect(useAppStore.getState().editingRequestId).toBeNull());
    expect(applyEvent).toHaveBeenCalledWith({
      type: 'request_updated',
      time: '14:00',
      request_id: '46393',
      engineer_id: null,
      request: { ...original, window_end: '18:00', skill: 'connection', transport_required: null },
    });
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({ lat: 55.7195, lon: 37.68 });
  });

  it('sends a point picked on the map', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<RequestEditDialog />);
    fireEvent.click(screen.getByRole('button', { name: 'Указать точку на карте' }));
    expect(useAppStore.getState().pickMode).toBe(true);
    expect(screen.getByRole('button', { name: 'Кликните по карте…' })).toBeDisabled();
    act(() => useAppStore.getState().finishPick({ lat: 55.72, lon: 37.69 }));
    expect(screen.getByText('Точка: 55.72000, 37.69000')).toBeInTheDocument();
    expect(screen.getByText('Изменится: точка на карте')).toBeInTheDocument();
    fireEvent.click(submitButton());
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({ address: original.address, lat: 55.72, lon: 37.69, geocode_precision: 'house' });
    expect(useAppStore.getState()).toMatchObject({ editingRequestId: null, pickedPoint: null });
  });

  it('stays open when the server rejects the change and closes on «Отмена»', async () => {
    const applyEvent = vi.fn().mockResolvedValue(false);
    useAppStore.setState({ applyEvent });
    render(<RequestEditDialog />);
    fireEvent.change(screen.getByLabelText('Длительность, мин'), { target: { value: '60' } });
    fireEvent.click(submitButton());
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(useAppStore.getState().editingRequestId).toBe('46393');
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(useAppStore.getState().editingRequestId).toBeNull();
  });

  it('ставит заявке отметку «Нужно оборудование» и называет изменение', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<RequestEditDialog />);
    const equipment = screen.getByLabelText('Нужно оборудование');
    expect(equipment).not.toBeChecked();

    fireEvent.click(equipment);
    expect(equipment).toBeChecked();
    expect(screen.getByText('Изменится: оборудование нужно')).toBeInTheDocument();
    fireEvent.click(submitButton());
    await waitFor(() => expect(useAppStore.getState().editingRequestId).toBeNull());
    expect(applyEvent).toHaveBeenCalledWith({
      type: 'request_updated',
      time: '13:30',
      request_id: '46393',
      engineer_id: null,
      request: { ...original, needs_equipment: true },
    });
  });

  it('снимает отметку с заявки, к которой оборудование везли', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:30', editingRequestId: '74198' });
    render(<RequestEditDialog />);
    const equipment = screen.getByLabelText('Нужно оборудование');
    expect(equipment).toBeChecked();
    expect(screen.queryByText(/Изменится/)).not.toBeInTheDocument();

    fireEvent.click(equipment);
    expect(screen.getByText('Изменится: оборудование не нужно')).toBeInTheDocument();
  });

  it('turns the request into «как можно скорее», hides the window and previews the change', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<RequestEditDialog />);
    const asap = screen.getByLabelText('Как можно скорее');
    expect(asap).not.toBeChecked();
    expect(isBefore(asap, screen.getByLabelText('Окно с'))).toBe(true);
    expect(screen.queryByText(ASAP_HINT)).not.toBeInTheDocument();

    fireEvent.click(asap);
    expect(screen.queryByLabelText('Окно с')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Окно до')).not.toBeInTheDocument();
    expect(screen.getByText(ASAP_HINT)).toHaveClass('muted');
    expect(screen.getByText('Изменится: как можно скорее')).toBeInTheDocument();
    fireEvent.click(submitButton());
    await waitFor(() => expect(useAppStore.getState().editingRequestId).toBeNull());
    expect(applyEvent).toHaveBeenCalledWith({
      type: 'request_updated',
      time: '13:30',
      request_id: '46393',
      engineer_id: null,
      request: { ...original, asap: true },
    });
  });

  it('prefills «как можно скорее» from the request and turns it back into a window', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    resetStore({ datasetId: 'd_test', state: makeAsapState(), clock: '13:30', editingRequestId: 'URG-002', applyEvent });
    render(<RequestEditDialog />);
    const asap = screen.getByLabelText('Как можно скорее');
    expect(asap).toBeChecked();
    expect(screen.queryByLabelText('Окно с')).not.toBeInTheDocument();
    expect(screen.getByText(ASAP_HINT)).toBeInTheDocument();
    expect(screen.queryByText(/Изменится/)).not.toBeInTheDocument();

    fireEvent.click(asap);
    expect([valueOf('Окно с'), valueOf('Окно до')]).toEqual(['13:00', '22:00']);
    expect(screen.queryByText(ASAP_HINT)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Окно с'), { target: { value: '15:00' } });
    fireEvent.change(screen.getByLabelText('Окно до'), { target: { value: '17:00' } });
    expect(screen.getByText('Изменится: окно вместо «как можно скорее»')).toBeInTheDocument();
    fireEvent.click(submitButton());
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0]).toEqual({
      type: 'request_updated',
      time: '13:30',
      request_id: 'URG-002',
      engineer_id: null,
      request: { ...makeAsapRequest(), asap: false, window_start: '15:00', window_end: '17:00' },
    });
  });

  it('renders nothing for a request that is not in the plan', () => {
    useAppStore.setState({ editingRequestId: 'GONE' });
    const { container } = render(<RequestEditDialog />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('EventToolbar', () => {
  it('opens the urgent request dialog with the time on the clock', () => {
    useAppStore.setState({ clock: '11:40' });
    render(<EventToolbar />);
    fireEvent.click(screen.getByRole('button', { name: 'Срочная заявка' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Срочная заявка' }));
    expect(dialog.getByLabelText('Время события')).toHaveValue('11:40');
  });

  it('keeps only the urgent request without its own event time: the time comes from the clock of the day', () => {
    const { container } = render(<EventToolbar />);
    expect(screen.queryByLabelText('Время события')).not.toBeInTheDocument();
    expect(container.querySelector('input')).toBeNull();
    expect(screen.queryByText(/раньше текущего времени|не может быть раньше/)).not.toBeInTheDocument();
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual(['Срочная заявка']);
  });

  it('keeps the open urgent request dialog in the store and closes it from the dialog', () => {
    render(<EventToolbar />);
    fireEvent.click(screen.getByRole('button', { name: 'Срочная заявка' }));
    expect(useAppStore.getState().toolbarDialog).toBe('urgent');
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Срочная заявка' })).getByRole('button', { name: 'Отмена' }));
    expect(useAppStore.getState().toolbarDialog).toBeNull();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('disables the urgent request while showing the plan before the event or replanning', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00', showPrevious: true });
    const view = render(<EventToolbar />);
    expect(screen.getByRole('button', { name: 'Срочная заявка' })).toBeDisabled();
    view.unmount();
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00', busy: true });
    render(<EventToolbar />);
    expect(screen.getByRole('button', { name: 'Срочная заявка' })).toBeDisabled();
  });
});
