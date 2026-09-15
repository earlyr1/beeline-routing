import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { EngineerUnavailableDialog } from './EngineerUnavailableDialog';
import { EventToolbar } from './EventToolbar';
import { UrgentRequestDialog } from './UrgentRequestDialog';

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
      window_start: '13:00',
      window_end: '15:00',
      duration_min: 60,
      skill: 'emergency',
      transport_required: 'car',
      priority: 'urgent',
    });
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
  it('offers only available engineers and submits the event', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    useAppStore.setState({ applyEvent, eventTime: '14:00' });
    render(<EngineerUnavailableDialog onClose={onClose} />);
    const options = screen.getAllByRole('option').map((option) => option.textContent);
    expect(options).toEqual(['Бригада Арташкин', 'Бригада Белузин']);
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
