import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { makeAsapState, makePlanningState } from '../../test/fixtures';
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
    expect(within(moved).getByText('Перенесена от «Бригада Белузин»')).toHaveAttribute(
      'title',
      'Перенос от «Бригада Белузин» к «Бригада Арташкин»',
    );

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

  it('opens the brigade page from the engineer of a row without selecting the row', () => {
    useAppStore.setState({ selectedRequestId: '46393' });
    render(<RequestsTab />);
    fireEvent.click(within(rowOf('URG-001')).getByRole('button', { name: 'Бригада Белузин' }));
    expect(useAppStore.getState()).toMatchObject({ selectedEngineerId: 'E02', selectedRequestId: null });
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

  it('shows the previous plan with upcoming changes marked and actions disabled', () => {
    useAppStore.setState({ showPrevious: true });
    render(<RequestsTab />);
    expect(within(rowOf('50104')).getByText('Бригада Белузин')).toBeInTheDocument();
    expect(within(rowOf('50104')).getByText('Будет перенесена к «Бригада Арташкин»')).toBeInTheDocument();
    expect(within(rowOf('URG-001')).getByText('Будет назначена')).toBeInTheDocument();
    expect(screen.queryByText('Перенесена от «Бригада Белузин»')).not.toBeInTheDocument();
    expect(within(rowOf('46393')).getByRole('button', { name: 'Отменить' })).toBeDisabled();
    expect(within(rowOf('46393')).getByRole('button', { name: 'Изменить' })).toBeDisabled();
  });

  it('opens the request edit from a row without selecting it', () => {
    render(<RequestsTab />);
    fireEvent.click(within(rowOf('50104')).getByRole('button', { name: 'Изменить' }));
    expect(useAppStore.getState().editingRequestId).toBe('50104');
    expect(useAppStore.getState().selectedRequestId).toBeNull();
  });

  it('blocks editing started work exactly like cancelling and allows editing a cancelled request', () => {
    render(<RequestsTab />);
    const started = within(rowOf('74198')).getByRole('button', { name: 'Изменить' });
    expect(started).toBeDisabled();
    expect(started).toHaveAttribute('title', 'Работа уже началась, изменить нельзя');
    expect(within(rowOf('74198')).getByRole('button', { name: 'Отменить' })).toBeDisabled();
    expect(within(rowOf('50104')).getByRole('button', { name: 'Изменить' })).not.toHaveAttribute('title');
    expect(within(rowOf('10135')).getByRole('button', { name: 'Изменить' })).toBeEnabled();
  });

  it('shows «как можно скорее» with the start of waiting instead of the window and a badge next to «Срочная»', () => {
    resetStore({ datasetId: 'd_test', state: makeAsapState(), eventTime: '13:30' });
    render(<RequestsTab />);
    const asap = rowOf('URG-002');
    expect(within(asap).getByText('Как можно скорее с 13:00')).toBeInTheDocument();
    expect(within(asap).queryByText(/^Окно/)).not.toBeInTheDocument();
    expect(Array.from(asap.querySelectorAll('.badge')).map((badge) => badge.textContent)).toEqual(['Срочная', 'Как можно скорее']);

    const windowed = rowOf('URG-001');
    expect(within(windowed).getByText('Окно 13:00–15:00')).toBeInTheDocument();
    expect(within(windowed).queryByText('Как можно скорее')).not.toBeInTheDocument();
  });

  it('disables editing while an event is being applied', () => {
    useAppStore.setState({ busy: true });
    render(<RequestsTab />);
    expect(within(rowOf('50104')).getByRole('button', { name: 'Изменить' })).toBeDisabled();
  });
});
