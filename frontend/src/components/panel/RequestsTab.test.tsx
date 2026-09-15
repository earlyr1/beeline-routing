import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { RequestsTab } from './RequestsTab';

const rowOf = (requestId: string) => screen.getByText(requestId).closest('li') as HTMLElement;

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '13:30' });
});

describe('RequestsTab', () => {
  it('lists requests with engineer, planned start and badges', () => {
    render(<RequestsTab />);
    const moved = rowOf('50104');
    expect(within(moved).getByText('Начало 14:00')).toBeInTheDocument();
    expect(within(moved).getByText('Бригада Арташкин')).toBeInTheDocument();
    expect(within(moved).getByText('Перенесена')).toBeInTheDocument();

    const urgent = rowOf('URG-001');
    expect(within(urgent).getByText('Срочная')).toBeInTheDocument();
    expect(within(urgent).getByText('Новое назначение')).toBeInTheDocument();

    expect(within(rowOf('18754')).getByText('Не назначена')).toBeInTheDocument();
    expect(within(rowOf('10135')).getByRole('button', { name: 'Вернуть' })).toBeEnabled();
  });

  it('cancels a request at the chosen event time without selecting the row', () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<RequestsTab />);
    fireEvent.click(within(rowOf('50104')).getByRole('button', { name: 'Отменить' }));
    expect(applyEvent).toHaveBeenCalledWith({ type: 'cancel', time: '13:30', request: null, request_id: '50104', engineer_id: null });
    expect(useAppStore.getState().selectedRequestId).toBeNull();
  });

  it('selects a request on row click', () => {
    render(<RequestsTab />);
    fireEvent.click(rowOf('46393'));
    expect(useAppStore.getState().selectedRequestId).toBe('46393');
  });

  it('filters by engineer in route order and blocks cancelling started work', () => {
    render(<RequestsTab />);
    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E01' } });
    const titles = screen.getAllByRole('listitem').map((item) => item.querySelector('strong')?.textContent);
    expect(titles).toEqual(['74198', '86160', '50104', '46393']);
    expect(within(rowOf('74198')).getByRole('button', { name: 'Отменить' })).toBeDisabled();
    expect(useAppStore.getState().selectedEngineerId).toBe('E01');
  });

  it('shows the previous plan without diff badges and with actions disabled', () => {
    useAppStore.setState({ showPrevious: true });
    render(<RequestsTab />);
    expect(within(rowOf('50104')).getByText('Бригада Белузин')).toBeInTheDocument();
    expect(screen.queryByText('Перенесена')).not.toBeInTheDocument();
    expect(within(rowOf('46393')).getByRole('button', { name: 'Отменить' })).toBeDisabled();
  });
});
