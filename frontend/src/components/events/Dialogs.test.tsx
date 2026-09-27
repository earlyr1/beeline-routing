import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, addTimelineEvent: vi.fn(), moveCursor: vi.fn(), postEvent: vi.fn(), clearTimeline: vi.fn(), setTimelineVariant: vi.fn() };
});

import * as api from '../../api/client';
import type { PlanEvent, PlanningState, ServiceRequest, Transport } from '../../api/types';
import { BEFORE_SHIFTS_HINT, cancelEvent, unavailableEvent } from '../../lib/events';
import { toMinutes } from '../../lib/format';
import { useAppStore, type AppState } from '../../store/useAppStore';
import {
  makeAsapRequest,
  makeAsapState,
  makeConfig,
  makeEventChoice,
  makePlanningState,
  makeTimeline,
  makeTimelineItem,
  WINDOW_GRID,
  WORK_TYPES,
} from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { ChoiceDialog } from './ChoiceDialog';
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

/** Диспетчер выбрал слот сетки в списке «Окно визита». */
const chooseSlot = (label: string) => {
  const option = within(screen.getByLabelText('Окно визита')).getByRole('option', { name: label }) as HTMLOptionElement;
  fireEvent.change(screen.getByLabelText('Окно визита'), { target: { value: option.value } });
};

beforeEach(() => {
  // Сетка окон визита приходит от сервера, типов работ в этом блоке нет: так выглядит диалог до их появления.
  resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00', config: makeConfig({ work_types: [] }) });
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
    // Клиенту называют слот: ближайший, в который бригада ещё успевает с часовыми работами (в 12:00–14:00 их уже не сделать).
    expect(event.request).toMatchObject({
      address: 'Город Москва, ул.Ташкентская, д. 16к2',
      window_start: '14:00',
      window_end: '16:00',
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
    resetStore({ datasetId: 'd_test', state, clock: '00:00', config: makeConfig({ work_types: [] }) });
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    expect(valueOf('Время события')).toBe('00:00');
    expect(valueOf('Окно визита')).toBe('10:00-12:00');
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

  it('offers the slots of the grid and moves the default one with the event time until the dispatcher picks', () => {
    resetStore({
      datasetId: 'd_test',
      state: makePlanningState({ now: '00:00' }),
      clock: '00:00',
      config: makeConfig({ work_types: [] }),
    });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    // Свободных полей времени нет: диспетчер предлагает клиенту слот, а не произвольный интервал.
    expect(screen.queryByLabelText('Окно с')).not.toBeInTheDocument();
    expect(optionsOf('Окно визита')).toEqual(WINDOW_GRID.map((slot) => `${slot.start}–${slot.end}`));

    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '09:00' } });
    expect(valueOf('Окно визита')).toBe('10:00-12:00');
    // В 13:30 до конца 12:00–14:00 остаётся полчаса, а работы идут час: предлагается слот, в который успеваем.
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '13:30' } });
    expect(valueOf('Окно визита')).toBe('14:00-16:00');
    // Прошедших слотов в списке нет, а идущий подписан: выбирать остаётся из того, что сервер примет.
    expect(optionsOf('Окно визита')).toEqual([
      '12:00–14:00 (идёт сейчас)',
      '14:00–16:00',
      '16:00–18:00',
      '18:00–20:00',
      '20:00–22:00',
    ]);

    // Диспетчер выбрал слот сам: время события его больше не двигает.
    chooseSlot('16:00–18:00');
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '14:00' } });
    expect(valueOf('Окно визита')).toBe('16:00-18:00');
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
    resetStore({ datasetId: 'd_test', state: { ...state, engineers }, clock: '13:00', applyEvent, config: makeConfig({ work_types: [] }) });
    render(<UrgentRequestDialog onClose={onClose} />);
    const asap = screen.getByLabelText('Как можно скорее');
    expect(asap).not.toBeChecked();
    expect(isBefore(asap, screen.getByLabelText('Окно визита'))).toBe(true);
    expect(screen.queryByText(ASAP_HINT)).not.toBeInTheDocument();

    chooseSlot('16:00–18:00');
    fireEvent.click(asap);
    expect(asap).toBeChecked();
    expect(screen.queryByLabelText('Окно визита')).not.toBeInTheDocument();
    expect(screen.getByText(ASAP_HINT)).toHaveClass('muted');
    expect(screen.getByLabelText('Длительность, мин')).toBeInTheDocument();

    fireEvent.click(asap);
    expect(asap).not.toBeChecked();
    expect(valueOf('Окно визита')).toBe('16:00-18:00');
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

describe('UrgentRequestDialog work types', () => {
  const config = makeConfig({ work_types: [] });
  const submit = () => screen.getByRole('button', { name: 'Добавить и перепланировать' });
  /** Подписи полей диалога по порядку: что диспетчер видит и может заполнить. */
  const fieldLabels = () =>
    Array.from(screen.getByRole('dialog', { name: 'Срочная заявка' }).querySelectorAll('.field > span, .field-check > span')).map(
      (label) => label.textContent,
    );
  const checkedOf = (label: string) => (screen.getByLabelText(label) as HTMLInputElement).checked;
  const chooseType = (title: string) => {
    const option = within(screen.getByLabelText('Тип работ')).getByRole('option', { name: title }) as HTMLOptionElement;
    fireEvent.change(screen.getByLabelText('Тип работ'), { target: { value: option.value } });
  };

  beforeEach(() => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00', config: makeConfig({ work_types: WORK_TYPES }) });
  });

  it('opens on «Авария» collapsed to the place and the time and sends it as soon as possible with the norms', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    useAppStore.setState({ applyEvent });
    render(<UrgentRequestDialog onClose={onClose} />);

    expect(optionsOf('Тип работ')).toEqual(['Авария', 'Подключение', 'Ремонт у клиента', 'Дозаказ оборудования']);
    expect(within(screen.getByLabelText('Тип работ')).getByRole('option', { name: 'Авария' })).toHaveProperty('selected', true);
    // Тип работ уже выбран: заполнить остаётся место и время события.
    expect(fieldLabels()).toEqual(['Тип работ', 'Адрес', 'Время события']);
    expect(screen.getByText('Авария · 80 мин · автомобиль · как можно скорее')).toBeInTheDocument();
    expect(screen.queryByLabelText('Навык')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Ташкентская, д. 16к2' } });
    fireEvent.click(submit());
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const event = applyEvent.mock.calls[0][0];
    expect(event).toMatchObject({ type: 'urgent', time: '13:00' });
    expect(event.request).toMatchObject({
      address: 'Город Москва, ул.Ташкентская, д. 16к2',
      skill: 'emergency',
      duration_min: 80,
      transport_required: 'car',
      needs_equipment: false,
      asap: true,
      window_start: '13:00',
      window_end: '22:00',
      priority: 'urgent',
      tier: 'emergency',
      source_type_bk: 'Глобальная проблема',
      source_type_hd: 'Авария',
    });
  });

  it('fills the fields from the norms of every work type and collapses again on «Авария»', () => {
    render(<UrgentRequestDialog onClose={() => undefined} />);
    const filled = () => ({
      duration: valueOf('Длительность, мин'),
      transport: valueOf('Транспорт'),
      equipment: checkedOf('Нужно оборудование'),
      asap: checkedOf('Как можно скорее'),
    });

    chooseType('Подключение');
    // Подключение ждёт своё окно: форма открыта целиком, окно по умолчанию с времени события.
    expect(fieldLabels()).toEqual([
      'Тип работ',
      'Адрес',
      'Как можно скорее',
      'Окно визита',
      'Длительность, мин',
      'Транспорт',
      'Нужно оборудование',
      'Время события',
    ]);
    expect(filled()).toEqual({ duration: '70', transport: '', equipment: true, asap: false });
    // В 13:00 в слот 12:00–14:00 с работами аварии уже не уложиться: по умолчанию подставлен следующий.
    expect(valueOf('Окно визита')).toBe('14:00-16:00');

    chooseType('Ремонт у клиента');
    expect(filled()).toEqual({ duration: '30', transport: '', equipment: false, asap: false });

    chooseType('Дозаказ оборудования');
    expect(filled()).toEqual({ duration: '20', transport: 'car', equipment: true, asap: false });

    // Поправленные руками значения новый тип работ заменяет своими нормативами.
    fireEvent.change(screen.getByLabelText('Длительность, мин'), { target: { value: '45' } });
    chooseType('Подключение');
    expect(filled()).toEqual({ duration: '70', transport: '', equipment: true, asap: false });

    chooseType('Авария');
    expect(fieldLabels()).toEqual(['Тип работ', 'Адрес', 'Время события']);
    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));
    expect(filled()).toEqual({ duration: '80', transport: 'car', equipment: false, asap: true });
  });

  it('lets the dispatcher override the norms of «Авария» after expanding them', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    useAppStore.setState({ applyEvent });
    render(<UrgentRequestDialog onClose={onClose} />);

    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));
    expect(screen.queryByText('Авария · 80 мин · автомобиль · как можно скорее')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Тип работ')).toHaveValue('Глобальная проблема');
    fireEvent.change(screen.getByLabelText('Длительность, мин'), { target: { value: '120' } });
    fireEvent.change(screen.getByLabelText('Транспорт'), { target: { value: '' } });
    fireEvent.click(screen.getByLabelText('Нужно оборудование'));
    fireEvent.click(screen.getByLabelText('Как можно скорее'));
    chooseSlot('16:00–18:00');
    fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Ташкентская, д. 16к2' } });
    fireEvent.click(submit());

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({
      skill: 'emergency',
      duration_min: 120,
      transport_required: null,
      needs_equipment: true,
      asap: false,
      window_start: '16:00',
      window_end: '18:00',
      source_type_bk: 'Глобальная проблема',
    });
  });

  it('collapses «Авария» again when the dispatcher expands it, picks another type and comes back', () => {
    render(<UrgentRequestDialog onClose={() => undefined} />);
    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));
    expect(fieldLabels()).toContain('Длительность, мин');

    chooseType('Подключение');
    chooseType('Авария');
    expect(fieldLabels()).toEqual(['Тип работ', 'Адрес', 'Время события']);
    expect(screen.getByText('Авария · 80 мин · автомобиль · как можно скорее')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Изменить' })).toBeInTheDocument();
  });

  it('moves the focus from «Изменить» to the first field it reveals', () => {
    render(<UrgentRequestDialog onClose={() => undefined} />);
    const change = screen.getByRole('button', { name: 'Изменить' });
    change.focus();
    fireEvent.click(change);
    expect(screen.getByLabelText('Как можно скорее')).toHaveFocus();

    // Тип работ без «как можно скорее» открывает форму сам, и фокус со списка типов он не уводит.
    const types = screen.getByLabelText('Тип работ');
    types.focus();
    chooseType('Подключение');
    expect(types).toHaveFocus();
  });

  it('explains next to the norm that the 20 minutes of road from the organisers are counted by the map', () => {
    render(<UrgentRequestDialog onClose={() => undefined} />);
    const hint = expect.stringContaining('у аварии 20 + 80 = 100 минут');
    expect(screen.getByText('Авария · 80 мин · автомобиль · как можно скорее')).toHaveAttribute('title', hint);
    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));
    expect(screen.getByLabelText('Длительность, мин').closest('label')).toHaveAttribute('title', hint);
  });

  it('adds «Авария» at a map point as in the demo: the collapsed form keeps the point and the address found for it', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    // Так стор оставляет «Добавить заявку здесь» из меню карты, когда адрес точки найден.
    useAppStore.setState({
      applyEvent,
      pickFor: 'urgent',
      pickedPoint: { lat: 55.71234, lon: 37.80123 },
      urgentAddressLookup: 'done',
      urgentSuggestedAddress: 'Москва, Перовская улица, 42к1',
    });
    render(<UrgentRequestDialog onClose={onClose} />);
    expect(fieldLabels()).toEqual(['Тип работ', 'Адрес', 'Время события']);
    expect(screen.getByText('Точка: 55.71234, 37.80123')).toBeInTheDocument();
    expect(valueOf('Адрес')).toBe('Москва, Перовская улица, 42к1');
    expect(screen.getByText('Авария · 80 мин · автомобиль · как можно скорее')).toBeInTheDocument();

    fireEvent.click(submit());
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({
      address: 'Москва, Перовская улица, 42к1',
      lat: 55.71234,
      lon: 37.80123,
      skill: 'emergency',
      duration_min: 80,
      transport_required: 'car',
      asap: true,
      window_start: '13:00',
      window_end: '22:00',
      source_type_bk: 'Глобальная проблема',
    });
  });

  it('keeps the old form with the skill and the time fields when the server sends neither work types nor the grid', () => {
    useAppStore.setState({ config: { ...config, window_grid: [] } });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    // Сетку окон задаёт сервер, своей у диалога нет: без неё окно вводят временем, как раньше.
    expect(screen.queryByLabelText('Окно визита')).not.toBeInTheDocument();
    expect([valueOf('Окно с'), valueOf('Окно до')]).toEqual(['13:00', '15:00']);
    expect(screen.queryByLabelText('Тип работ')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Изменить' })).not.toBeInTheDocument();
    expect([valueOf('Навык'), valueOf('Длительность, мин'), valueOf('Транспорт')]).toEqual(['emergency', '60', 'car']);
    // Час без типа работ — не норматив, и подсказки о нормативе у длительности нет.
    expect(screen.getByLabelText('Длительность, мин').closest('label')).not.toHaveAttribute('title');
    expect(screen.getByLabelText('Как можно скорее')).not.toHaveFocus();
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

  it('takes the brigade and the time «Задержка с» from the request card and refills for another request', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '14:10', delayDialogOpen: true, delayEngineerId: 'E01', delayTime: '14:45' });
    const view = render(<EngineerDelayDialog />);
    expect([valueOf('Инженер'), valueOf('На сколько минут'), valueOf('Задержка с')]).toEqual(['E01', '30', '14:45']);
    // Визитов бригады после 14:45 — те, что сдвинет задержка.
    expect(optionsOf('Инженер')[0]).toBe('Бригада Арташкин (визитов после 14:45: 1)');
    fireEvent.change(screen.getByLabelText('Задержка с'), { target: { value: '15:00' } });

    // Та же бригада из карточки другой заявки: форма заполняется заново её временем.
    act(() => useAppStore.getState().startDelay('E01', '14:20'));
    view.rerender(<EngineerDelayDialog />);
    expect(valueOf('Задержка с')).toBe('14:20');
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

  it('renders the delay dialog only while it is open', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00', delayDialogOpen: false, delayEngineerId: 'E01' });
    expect(render(<EngineerDelayDialog />).container).toBeEmptyDOMElement();
  });

  it('warns in every event dialog that an event before the shifts replans the whole day', () => {
    const early = { datasetId: 'd_test', state: makePlanningState(), clock: '08:30' };
    const dialogs = [
      { patch: {}, element: <UrgentRequestDialog onClose={() => undefined} />, time: 'Время события' },
      { patch: { delayDialogOpen: true, delayEngineerId: 'E01' }, element: <EngineerDelayDialog />, time: 'Задержка с' },
      { patch: { engineerDialog: { kind: 'unavailable' as const, engineerId: 'E01' } }, element: <EngineerUnavailableDialog />, time: 'Недоступен с' },
      { patch: { engineerDialog: { kind: 'transport' as const, engineerId: 'E01' } }, element: <TransportChangeDialog />, time: 'Сменить с' },
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
      ['d_test', unavailableEvent('E02', '13:00'), undefined],
      ['d_test', unavailableEvent('E02', '13:00'), undefined],
    ]);
    expect(useAppStore.getState().state?.version).toBe(5);
    expect(api.postEvent).not.toHaveBeenCalled();
    expect(api.moveCursor).not.toHaveBeenCalled();
  });

  it('sends an event earlier than the clock to the timeline as it is, without moving the plan', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
    vi.mocked(api.addTimelineEvent).mockResolvedValue(makePlanningState({ version: 5 }));
    expect(await useAppStore.getState().applyEvent(cancelEvent('46393', '11:30'))).toBe(true);
    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', cancelEvent('46393', '11:30'), undefined);
    expect(api.postEvent).not.toHaveBeenCalled();
  });
});

describe('event dialogs when the server asks for a variant', () => {
  /** Ответ сервера на событие: время встало на нём, и «Ничего не менять» ломает план больше пересчёта. */
  const asking = (event: PlanEvent): PlanningState =>
    makePlanningState({
      cursor: '13:30',
      version: 5,
      pending_choice: makeEventChoice({ entry_id: 'tl_9', event }),
      timeline: [makeTimelineItem({ id: 'tl_9', event, status: 'awaiting', variant: null })],
    });

  const submit = (name: string) => fireEvent.click(screen.getByRole('button', { name }));

  const flows: [string, Partial<AppState>, () => JSX.Element, () => void, (state: AppState) => boolean][] = [
    [
      'the edit of a request',
      { editingRequestId: '46393' },
      () => <RequestEditDialog />,
      () => {
        fireEvent.change(screen.getByLabelText('Длительность, мин'), { target: { value: '60' } });
        submit('Сохранить и перепланировать');
      },
      (state) => state.editingRequestId === null,
    ],
    ['the delay', { delayDialogOpen: true, delayEngineerId: 'E01' }, () => <EngineerDelayDialog />, () => submit('Перепланировать'), (state) => !state.delayDialogOpen],
    [
      'the change of transport',
      { engineerDialog: { kind: 'transport', engineerId: 'E01' } },
      () => <TransportChangeDialog />,
      () => submit('Перепланировать'),
      (state) => state.engineerDialog === null,
    ],
    [
      'the unavailable engineer',
      { engineerDialog: { kind: 'unavailable', engineerId: 'E02' } },
      () => <EngineerUnavailableDialog />,
      () => submit('Перепланировать'),
      (state) => state.engineerDialog === null,
    ],
    [
      'the urgent request',
      { toolbarDialog: 'urgent' },
      () => <EventToolbar />,
      () => {
        fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Ташкентская, д. 16к2' } });
        submit('Добавить и перепланировать');
      },
      (state) => state.toolbarDialog === null,
    ],
  ];

  beforeEach(() => {
    vi.mocked(api.addTimelineEvent).mockReset();
    vi.mocked(api.moveCursor).mockReset();
    vi.mocked(api.setTimelineVariant).mockReset();
  });

  it.each(flows)('closes %s, opens the choice at the clock and keeps the clock after it', async (_, patch, dialog, fill, closed) => {
    resetStore({ datasetId: 'd_test', state: makePlanningState({ cursor: '13:30' }), config: makeConfig({ work_types: [] }), ...patch });
    vi.mocked(api.addTimelineEvent).mockImplementation(async (_dataset, event) => asking(event));
    render(
      <>
        {dialog()}
        <ChoiceDialog />
      </>,
    );
    fill();
    await waitFor(() => expect(closed(useAppStore.getState())).toBe(true));
    // Стратегию событию выбирает не диалог: без variant сервер сам решает, нужно ли окно.
    expect(vi.mocked(api.addTimelineEvent).mock.calls[0][2]).toBeUndefined();
    const choice = screen.getByRole('dialog');
    expect(within(choice).getByText('Как исправить план')).toBeInTheDocument();
    expect(useAppStore.getState()).toMatchObject({ clock: '13:30', playing: false, busy: false, resumeAfterChoice: { time: '13:30', play: false } });

    vi.mocked(api.setTimelineVariant).mockResolvedValue(makePlanningState({ cursor: '13:30', version: 6 }));
    fireEvent.click(within(within(choice).getByRole('article', { name: 'Оптимально по дню' })).getByRole('button', { name: 'Выбрать' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(api.setTimelineVariant).toHaveBeenCalledWith('d_test', 'tl_9', 'optimal');
    expect(api.moveCursor).not.toHaveBeenCalled();
    expect(useAppStore.getState()).toMatchObject({ clock: '13:30', choice: null });
  });

  it('closes the dialog without a choice window when the server kept the routes as they are', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState({ cursor: '13:30' }), editingRequestId: '46393' });
    vi.mocked(api.addTimelineEvent).mockImplementation(async (_dataset, event) =>
      makePlanningState({
        cursor: '13:30',
        version: 5,
        timeline: [makeTimelineItem({ id: 'tl_9', event, status: 'applied', variant: 'keep', variant_auto: true })],
      }),
    );
    render(
      <>
        <RequestEditDialog />
        <ChoiceDialog />
      </>,
    );
    fireEvent.change(screen.getByLabelText('Длительность, мин'), { target: { value: '60' } });
    submit('Сохранить и перепланировать');
    await waitFor(() => expect(useAppStore.getState().editingRequestId).toBeNull());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(useAppStore.getState()).toMatchObject({ choice: null, choiceLoading: false, clock: '13:30' });
    expect(useAppStore.getState().state?.version).toBe(5);
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
    resetStore({
      datasetId: 'd_test',
      state: makePlanningState(),
      clock: '13:30',
      editingRequestId: '46393',
      config: makeConfig({ work_types: [] }),
    });
  });

  it('prefills the form from the current request', () => {
    render(<RequestEditDialog />);
    expect(screen.getByRole('dialog', { name: 'Изменить заявку' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Изменить заявку 46393' })).toBeInTheDocument();
    expect(valueOf('Адрес')).toBe('Город Москва, ул.Шарикоподшипниковская, д. 14');
    // Окно заявки 15:00–17:00 не с сетки (так пришло из данных): список показывает его как есть, не подменяя молча.
    // Слотов, закончившихся к времени события (13:30), в списке нет, а идущий подписан.
    expect([valueOf('Окно визита'), valueOf('Длительность, мин')]).toEqual(['', '45']);
    expect(optionsOf('Окно визита')).toEqual([
      '15:00–17:00',
      '12:00–14:00 (идёт сейчас)',
      '14:00–16:00',
      '16:00–18:00',
      '18:00–20:00',
      '20:00–22:00',
    ]);
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

  it('checks the event time and lets the window of the data through while the dispatcher keeps it', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<RequestEditDialog />);
    // Окно заявки не с сетки, но диспетчер его не трогал: менять длительность это не мешает (так смотрит и сервер).
    fireEvent.change(screen.getByLabelText('Длительность, мин'), { target: { value: '60' } });
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '' } });
    fireEvent.click(submitButton());
    expect(await screen.findByText('Укажите время в формате ЧЧ:ММ')).toBeInTheDocument();
    expect(screen.queryByText(/Выберите окно визита из сетки/)).not.toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();
  });

  it('moves the window only to a slot of the grid', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<RequestEditDialog />);
    chooseSlot('16:00–18:00');
    expect(screen.getByText('Изменится: окно 15:00–17:00 → 16:00–18:00')).toBeInTheDocument();
    fireEvent.click(submitButton());
    await waitFor(() => expect(useAppStore.getState().editingRequestId).toBeNull());
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({ window_start: '16:00', window_end: '18:00' });
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
    chooseSlot('16:00–18:00');
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
      request: { ...original, window_start: '16:00', window_end: '18:00', skill: 'connection', transport_required: null },
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
    expect(isBefore(asap, screen.getByLabelText('Окно визита'))).toBe(true);
    expect(screen.queryByText(ASAP_HINT)).not.toBeInTheDocument();

    fireEvent.click(asap);
    expect(screen.queryByLabelText('Окно визита')).not.toBeInTheDocument();
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

  it('asks for a slot of the grid when «Как можно скорее» is turned off', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    resetStore({
      datasetId: 'd_test',
      state: makeAsapState(),
      clock: '13:30',
      editingRequestId: 'URG-002',
      applyEvent,
      config: makeConfig({ work_types: [] }),
    });
    render(<RequestEditDialog />);
    const asap = screen.getByLabelText('Как можно скорее');
    expect(asap).toBeChecked();

    fireEvent.click(asap);
    // Окно 13:00–22:00 заявке задал сервер, и клиенту его не называли: в списке пусто, пока слот не выбран.
    expect(valueOf('Окно визита')).toBe('');
    expect(within(screen.getByLabelText('Окно визита')).getByRole('option', { name: 'Выберите окно' })).toBeInTheDocument();
    fireEvent.click(submitButton());
    expect(
      await screen.findByText(
        'Выберите окно визита из сетки: 10:00–12:00, 12:00–14:00, 14:00–16:00, 16:00–18:00, 18:00–20:00, 20:00–22:00',
      ),
    ).toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();

    chooseSlot('16:00–18:00');
    fireEvent.click(submitButton());
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({ asap: false, window_start: '16:00', window_end: '18:00' });
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
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual(['Сброс событий', 'Срочная заявка']);
  });

  it('keeps the open urgent request dialog in the store and closes it from the dialog', () => {
    render(<EventToolbar />);
    fireEvent.click(screen.getByRole('button', { name: 'Срочная заявка' }));
    expect(useAppStore.getState().toolbarDialog).toBe('urgent');
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Срочная заявка' })).getByRole('button', { name: 'Отмена' }));
    expect(useAppStore.getState().toolbarDialog).toBeNull();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('resets the events after a confirmation and puts the clock at the start of the day', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState({ timeline: makeTimeline() }), clock: '15:00' });
    vi.mocked(api.clearTimeline).mockResolvedValue(makePlanningState({ timeline: [], cursor: '00:00', version: 1 }));
    vi.mocked(api.moveCursor).mockResolvedValue(makePlanningState({ timeline: [], cursor: '09:00', version: 1 }));
    render(<EventToolbar />);

    fireEvent.click(screen.getByRole('button', { name: 'Сброс событий' }));
    const confirm = within(screen.getByRole('dialog', { name: 'Сброс событий' }));
    fireEvent.click(confirm.getByRole('button', { name: 'Отмена' }));
    expect(screen.queryByRole('dialog', { name: 'Сброс событий' })).not.toBeInTheDocument();
    expect(api.clearTimeline).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Сброс событий' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Сброс событий' })).getByRole('button', { name: 'Сбросить' }));

    await waitFor(() => expect(useAppStore.getState().clock).toBe('09:00'));
    expect(api.clearTimeline).toHaveBeenCalledWith('d_test');
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', '09:00']]);
    expect(useAppStore.getState().state?.timeline).toEqual([]);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Сброс событий' })).not.toBeInTheDocument());
    // Шкала пустая: сбрасывать нечего.
    expect(screen.getByRole('button', { name: 'Сброс событий' })).toBeDisabled();
  });

  it('closes the reset confirmation on Escape and does not reset while the clock plays', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState({ timeline: makeTimeline() }), clock: '15:00' });
    const view = render(<EventToolbar />);
    fireEvent.click(screen.getByRole('button', { name: 'Сброс событий' }));
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Сброс событий' })).not.toBeInTheDocument();
    view.unmount();

    resetStore({ datasetId: 'd_test', state: makePlanningState({ timeline: makeTimeline() }), clock: '15:00', playing: true });
    render(<EventToolbar />);
    expect(screen.getByRole('button', { name: 'Сброс событий' })).toBeDisabled();
  });

  it('disables the urgent request while replanning', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00', busy: true });
    render(<EventToolbar />);
    expect(screen.getByRole('button', { name: 'Срочная заявка' })).toBeDisabled();
  });
});
