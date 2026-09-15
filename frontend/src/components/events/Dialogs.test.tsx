import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toMinutes } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { EngineerUnavailableDialog } from './EngineerUnavailableDialog';
import { EventToolbar } from './EventToolbar';
import { UrgentRequestDialog } from './UrgentRequestDialog';

const valueOf = (label: string) => (screen.getByLabelText(label) as HTMLInputElement).value;

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

describe('EventToolbar', () => {
  it('rejects an event time earlier than now and opens dialogs', () => {
    render(<EventToolbar />);
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '12:00' } });
    expect(screen.getByText('Время события не может быть раньше 13:00')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Инженер недоступен' }));
    expect(screen.getByRole('dialog', { name: 'Инженер недоступен' })).toBeInTheDocument();
  });
});
