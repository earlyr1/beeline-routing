import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PlanningState, ServiceRequest, Transport } from '../../api/types';
import { toMinutes } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { EngineerDelayDialog } from './EngineerDelayDialog';
import { EngineerUnavailableDialog } from './EngineerUnavailableDialog';
import { EventToolbar } from './EventToolbar';
import { RequestEditDialog } from './RequestEditDialog';
import { TransportChangeDialog } from './TransportChangeDialog';
import { UrgentRequestDialog } from './UrgentRequestDialog';

const valueOf = (label: string) => (screen.getByLabelText(label) as HTMLInputElement).value;
const optionsOf = (label: string) => within(screen.getByLabelText(label)).getAllByRole('option').map((option) => option.textContent);

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
  resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '13:00' });
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

    fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Ташкентская, д. 16к2' } });
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '12:00' } });
    fireEvent.click(submit);
    expect(await screen.findByText('Время события не может быть раньше 13:00')).toBeInTheDocument();

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
    });
  });

  it('starts the default window inside working hours right after planning the day', async () => {
    const state = makePlanningState({ now: '00:00' });
    resetStore({ datasetId: 'd_test', state, eventTime: '00:00' });
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    expect(valueOf('Время события')).toBe('00:00');
    expect([valueOf('Окно с'), valueOf('Окно до')]).toEqual(['10:00', '12:00']);

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
    resetStore({ datasetId: 'd_test', state: makePlanningState({ now: '00:00' }), eventTime: '00:00' });
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
      eventTime: '13:00',
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
      eventTime: '13:00',
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
      eventTime: '13:00',
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

describe('map point ownership', () => {
  const noop = () => undefined;
  const dialog = (name: string) => within(screen.getByRole('dialog', { name }));

  beforeEach(() => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '13:30', editingRequestId: '46393' });
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
    resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '14:00', delayDialogOpen: true });
  });

  it('preselects the busiest engineer, sets the minutes from presets and submits the delay', async () => {
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

  it('preselects the engineer of the route card and keeps it when the time changes', () => {
    resetStore({ datasetId: 'd_test', state: withBusyBeluzin(makePlanningState()), eventTime: '13:00', delayDialogOpen: true, delayEngineerId: 'E01' });
    render(<EngineerDelayDialog />);
    expect(valueOf('Инженер')).toBe('E01');
    fireEvent.change(screen.getByLabelText('Задержка с'), { target: { value: '16:00' } });
    expect(valueOf('Инженер')).toBe('E01');
  });

  it('follows the busiest engineer when the time changes until the dispatcher picks one', () => {
    resetStore({ datasetId: 'd_test', state: withBusyBeluzin(makePlanningState()), eventTime: '13:00', delayDialogOpen: true });
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
    fireEvent.change(screen.getByLabelText('Задержка с'), { target: { value: '12:00' } });
    fireEvent.click(submitButton());
    expect(await screen.findByText('Время события не может быть раньше 13:00')).toBeInTheDocument();
    expect(screen.queryByText('Задержка должна быть от 5 до 480 минут')).not.toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();
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
    resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '14:00', engineerDialog: { kind: 'unavailable', engineerId: 'E02' } });
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
      eventTime: '13:00',
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
      eventTime: '13:00',
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

describe('TransportChangeDialog', () => {
  beforeEach(() => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '13:00', engineerDialog: { kind: 'transport', engineerId: 'E01' } });
  });

  it('opens as a floating dialog for the engineer of the brigade page, suggests a bike instead of the car and submits', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent, eventTime: '14:00' });
    render(<TransportChangeDialog />);
    expect(screen.getByRole('dialog', { name: 'Смена транспорта' })).toHaveClass('dialog', 'dialog--floating');
    expect(optionsOf('Инженер')).toEqual([
      'Бригада Арташкин · Автомобиль (визитов после 14:00: 2)',
      'Бригада Белузин · Автомобиль (визитов после 14:00: 0)',
    ]);
    expect(valueOf('Инженер')).toBe('E01');
    expect(valueOf('Сменить с')).toBe('14:00');
    expect(optionsOf('Новый транспорт')).toEqual(['Пешеход', 'Велосипед', 'Общественный транспорт']);
    expect(valueOf('Новый транспорт')).toBe('bike');
    expect(screen.getByText('Заявок с требованием «Автомобиль» после 14:00: 1, их перераспределит оптимизатор')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Новый транспорт'), { target: { value: 'foot' } });
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    await waitFor(() => expect(useAppStore.getState().engineerDialog).toBeNull());
    expect(applyEvent).toHaveBeenCalledWith({
      type: 'engineer_transport_changed',
      time: '14:00',
      request: null,
      request_id: null,
      engineer_id: 'E01',
      transport: 'foot',
    });
  });

  it('hides the car hint when no car-only requests are left after the time', () => {
    useAppStore.setState({ eventTime: '16:00' });
    render(<TransportChangeDialog />);
    expect(valueOf('Новый транспорт')).toBe('bike');
    expect(screen.queryByText(/Заявок с требованием/)).not.toBeInTheDocument();
  });

  it('resets the new transport to the default of the chosen engineer until the dispatcher picks one', () => {
    resetStore({
      datasetId: 'd_test',
      state: withTransport(makePlanningState(), 'E02', 'foot'),
      eventTime: '13:00',
      engineerDialog: { kind: 'transport', engineerId: 'E01' },
    });
    render(<TransportChangeDialog />);
    expect(valueOf('Инженер')).toBe('E01');
    expect(valueOf('Новый транспорт')).toBe('bike');

    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E02' } });
    expect(valueOf('Новый транспорт')).toBe('car');
    expect(optionsOf('Новый транспорт')).toEqual(['Автомобиль', 'Велосипед', 'Общественный транспорт']);
    expect(screen.getByText('Инженер сможет брать заявки, которым нужен автомобиль')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E01' } });
    expect(valueOf('Новый транспорт')).toBe('bike');
    expect(screen.queryByText('Инженер сможет брать заявки, которым нужен автомобиль')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Новый транспорт'), { target: { value: 'public' } });
    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E02' } });
    expect(valueOf('Новый транспорт')).toBe('public');
  });

  it('keeps the engineer of the brigade page and its transport when the time changes', () => {
    const state = withTransport(withBusyBeluzin(makePlanningState()), 'E02', 'foot');
    resetStore({ datasetId: 'd_test', state, eventTime: '13:00', engineerDialog: { kind: 'transport', engineerId: 'E01' } });
    render(<TransportChangeDialog />);
    expect(valueOf('Инженер')).toBe('E01');
    expect(valueOf('Новый транспорт')).toBe('bike');
    fireEvent.change(screen.getByLabelText('Сменить с'), { target: { value: '16:00' } });
    expect(valueOf('Инженер')).toBe('E01');
    expect(valueOf('Новый транспорт')).toBe('bike');
  });

  it('falls back to the busiest available engineer when the brigade is already unavailable', () => {
    const state = withTransport(withBusyBeluzin(makePlanningState()), 'E02', 'foot');
    resetStore({ datasetId: 'd_test', state, eventTime: '13:00', engineerDialog: { kind: 'transport', engineerId: 'E03' } });
    render(<TransportChangeDialog />);
    expect(valueOf('Инженер')).toBe('E02');
    expect(screen.getByRole('option', { name: 'Бригада Белузин · Пешеход (визитов после 13:00: 3)' })).toBeInTheDocument();
    expect(valueOf('Новый транспорт')).toBe('car');

    fireEvent.change(screen.getByLabelText('Сменить с'), { target: { value: '16:00' } });
    expect(valueOf('Инженер')).toBe('E01');
    expect(valueOf('Новый транспорт')).toBe('bike');
  });

  it('rejects a time earlier than now', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<TransportChangeDialog />);
    expect(valueOf('Сменить с')).toBe('13:00');
    fireEvent.change(screen.getByLabelText('Сменить с'), { target: { value: '12:00' } });
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    expect(await screen.findByText('Время события не может быть раньше 13:00')).toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();
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
    resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '13:30', editingRequestId: '46393' });
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
    expect(optionsOf('Транспорт')).toEqual(['Не требуется', 'Автомобиль', 'Пешеход', 'Велосипед', 'Общественный транспорт']);
    expect(screen.queryByText(/Изменится/)).not.toBeInTheDocument();
  });

  it('defaults the event time to now when the toolbar time is earlier', () => {
    useAppStore.setState({ eventTime: '12:00' });
    render(<RequestEditDialog />);
    expect(valueOf('Время события')).toBe('13:00');
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
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '12:00' } });
    fireEvent.click(submitButton());
    expect(await screen.findByText('Конец окна должен быть позже начала')).toBeInTheDocument();
    expect(screen.getByText('Время события не может быть раньше 13:00')).toBeInTheDocument();
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

  it('renders nothing for a request that is not in the plan', () => {
    useAppStore.setState({ editingRequestId: 'GONE' });
    const { container } = render(<RequestEditDialog />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('EventToolbar', () => {
  it('rejects an event time earlier than now and opens the urgent request dialog', () => {
    render(<EventToolbar />);
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '12:00' } });
    expect(screen.getByText('Время события не может быть раньше 13:00')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Срочная заявка' }));
    expect(screen.getByRole('dialog', { name: 'Срочная заявка' })).toBeInTheDocument();
  });

  it('keeps only the event time and the urgent request: engineer events open from the brigade page', () => {
    render(<EventToolbar />);
    expect(screen.getByLabelText('Время события')).toBeInTheDocument();
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
    resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '13:00', showPrevious: true });
    const view = render(<EventToolbar />);
    expect(screen.getByRole('button', { name: 'Срочная заявка' })).toBeDisabled();
    view.unmount();
    resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '13:00', busy: true });
    render(<EventToolbar />);
    expect(screen.getByRole('button', { name: 'Срочная заявка' })).toBeDisabled();
  });
});
