import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PlanningState, ServiceRequest, Transport } from '../../api/types';
import { toMinutes } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
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
    useAppStore.setState({ pickedPoint: { lat: 55.71234, lon: 37.80123 } });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    expect(screen.getByText('Точка: 55.71234, 37.80123')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Указать точку на карте' }));
    expect(useAppStore.getState().pickMode).toBe(true);
    expect(screen.getByRole('button', { name: 'Кликните по карте…' })).toBeDisabled();
  });
});

describe('EngineerUnavailableDialog', () => {
  it('offers only available engineers with their remaining visits and submits the event', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    useAppStore.setState({ applyEvent, eventTime: '14:00' });
    render(<EngineerUnavailableDialog onClose={onClose} />);
    const options = screen.getAllByRole('option').map((option) => option.textContent);
    expect(options).toEqual(['Бригада Арташкин (визитов после 14:00: 2)', 'Бригада Белузин (визитов после 14:00: 0)']);
    expect(valueOf('Инженер')).toBe('E01');
    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E02' } });
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(applyEvent).toHaveBeenCalledWith({
      type: 'engineer_unavailable',
      time: '14:00',
      request: null,
      request_id: null,
      engineer_id: 'E02',
    });
  });

  it('preselects the engineer with the most visits after the chosen time', () => {
    const state = makePlanningState();
    const [e01, e02, e03] = state.plan.routes;
    const plan = {
      ...state.plan,
      routes: [{ ...e01, visits: e01.visits.slice(0, 2) }, { ...e02, visits: [...e02.visits, ...e01.visits.slice(2)] }, e03],
    };
    resetStore({ datasetId: 'd_test', state: { ...state, plan }, eventTime: '13:00' });
    render(<EngineerUnavailableDialog onClose={() => undefined} />);
    expect(valueOf('Инженер')).toBe('E02');
    expect(screen.getByRole('option', { name: 'Бригада Белузин (визитов после 13:00: 3)' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Недоступен с'), { target: { value: '16:00' } });
    expect(screen.getByRole('option', { name: 'Бригада Белузин (визитов после 16:00: 0)' })).toBeInTheDocument();
    expect(valueOf('Инженер')).toBe('E01');

    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E02' } });
    fireEvent.change(screen.getByLabelText('Недоступен с'), { target: { value: '13:30' } });
    expect(valueOf('Инженер')).toBe('E02');
  });
});

describe('TransportChangeDialog', () => {
  it('preselects the busiest engineer, suggests a bike instead of the car and submits the change', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    useAppStore.setState({ applyEvent, eventTime: '14:00' });
    render(<TransportChangeDialog onClose={onClose} />);
    expect(screen.getByRole('dialog', { name: 'Смена транспорта' })).toBeInTheDocument();
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
    await waitFor(() => expect(onClose).toHaveBeenCalled());
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
    render(<TransportChangeDialog onClose={() => undefined} />);
    expect(valueOf('Новый транспорт')).toBe('bike');
    expect(screen.queryByText(/Заявок с требованием/)).not.toBeInTheDocument();
  });

  it('resets the new transport to the default of the chosen engineer until the dispatcher picks one', () => {
    resetStore({ datasetId: 'd_test', state: withTransport(makePlanningState(), 'E02', 'foot'), eventTime: '13:00' });
    render(<TransportChangeDialog onClose={() => undefined} />);
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

  it('follows the busiest engineer when the time changes and resets the transport with it', () => {
    const state = withTransport(withBusyBeluzin(makePlanningState()), 'E02', 'foot');
    resetStore({ datasetId: 'd_test', state, eventTime: '13:00' });
    render(<TransportChangeDialog onClose={() => undefined} />);
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
    render(<TransportChangeDialog onClose={() => undefined} />);
    expect(valueOf('Сменить с')).toBe('13:00');
    fireEvent.change(screen.getByLabelText('Сменить с'), { target: { value: '12:00' } });
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    expect(await screen.findByText('Время события не может быть раньше 13:00')).toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();
  });

  it('stays open when the server rejects the change', async () => {
    const applyEvent = vi.fn().mockResolvedValue(false);
    const onClose = vi.fn();
    useAppStore.setState({ applyEvent });
    render(<TransportChangeDialog onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(onClose).toHaveBeenCalled();
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
  it('rejects an event time earlier than now and opens dialogs', () => {
    render(<EventToolbar />);
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '12:00' } });
    expect(screen.getByText('Время события не может быть раньше 13:00')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Инженер недоступен' }));
    expect(screen.getByRole('dialog', { name: 'Инженер недоступен' })).toBeInTheDocument();
  });

  it('opens the transport change dialog from a button between the urgent request and the unavailability', () => {
    render(<EventToolbar />);
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Срочная заявка',
      'Смена транспорта',
      'Инженер недоступен',
    ]);
    fireEvent.click(screen.getByRole('button', { name: 'Смена транспорта' }));
    expect(screen.getByRole('dialog', { name: 'Смена транспорта' })).toBeInTheDocument();
  });

  it('disables the transport change while showing the plan before the event', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '13:00', showPrevious: true });
    render(<EventToolbar />);
    expect(screen.getByRole('button', { name: 'Смена транспорта' })).toBeDisabled();
  });
});
