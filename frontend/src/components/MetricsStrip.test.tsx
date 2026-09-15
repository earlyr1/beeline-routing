import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, buildPlan: vi.fn() };
});

import * as api from '../api/client';
import { useAppStore } from '../store/useAppStore';
import { makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { MetricsStrip } from './MetricsStrip';

beforeEach(() => {
  vi.resetAllMocks();
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('MetricsStrip', () => {
  it('compares the current plan with the baseline', () => {
    render(<MetricsStrip />);
    expect(screen.getByText('+2,7 км к базовому')).toBeInTheDocument();
    expect(screen.getAllByText(/к базовому/)).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Другой файл' })).toBeEnabled();
  });

  it('hides baseline deltas while the plan before the event is shown', () => {
    useAppStore.setState({ showPrevious: true });
    render(<MetricsStrip />);
    expect(screen.getByText(/показан план до события/)).toBeInTheDocument();
    expect(screen.queryByText(/к базовому/)).not.toBeInTheDocument();
  });

  it('blocks switching to another file while a calculation is running', () => {
    useAppStore.setState({ busy: true });
    render(<MetricsStrip />);
    expect(screen.getByRole('button', { name: 'Другой файл' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Пересчитать с нуля' })).toBeDisabled();
  });

  it('shows the workload level of the day and replans from scratch with the same level', async () => {
    useAppStore.getState().setPlanningState(makePlanningState({ workload_level: 3 }));
    vi.mocked(api.buildPlan).mockResolvedValue(makePlanningState({ workload_level: 3, version: 5 }));
    render(<MetricsStrip />);

    const item = screen.getByTitle('Стоимость нового инженера для оптимизатора зависит от нагрузки дня');
    expect(item).toHaveTextContent('Нагрузка');
    expect(item).toHaveTextContent('😓 Плотный день');

    fireEvent.click(screen.getByRole('button', { name: 'Пересчитать с нуля' }));
    await waitFor(() => expect(useAppStore.getState().state?.version).toBe(5));
    expect(api.buildPlan).toHaveBeenCalledWith('d_test', 3);
  });
});
