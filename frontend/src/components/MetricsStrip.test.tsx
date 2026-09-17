import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, buildPlan: vi.fn(), moveCursor: vi.fn() };
});

import * as api from '../api/client';
import { useAppStore } from '../store/useAppStore';
import { makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { MetricsStrip } from './MetricsStrip';

const workload = () => screen.getByRole('combobox', { name: 'Нагрузка инженеров' }) as HTMLSelectElement;
const lunch = () => screen.getByRole('button', { name: 'Обед по плану' });
const apply = () => screen.getByRole('button', { name: 'Применить' });
const queryApply = () => screen.queryByRole('button', { name: 'Применить' });

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
    // Пересчёта с нуля в шапке нет: день пересчитывает «Применить», события сбрасывает панель событий.
    expect(screen.queryByRole('button', { name: 'Пересчитать с нуля' })).not.toBeInTheDocument();
  });

  it('shows the workload level and the lunch of the session, not the choice of the store', () => {
    useAppStore.getState().setPlanningState(makePlanningState({ workload_level: 2, lunch_enabled: false }));
    useAppStore.getState().setWorkloadLevel(0);
    useAppStore.getState().setLunchEnabled(true);
    render(<MetricsStrip />);

    expect(screen.getByText('Нагрузка')).toBeInTheDocument();
    expect(workload().value).toBe('2');
    expect(Array.from(workload().options).map((option) => option.textContent)).toEqual([
      '😌 Спокойный день',
      '😐 Обычный день',
      '🥵 На пределе',
    ]);
    expect(lunch()).toHaveTextContent('без обеда');
    expect(lunch()).toHaveAttribute('aria-pressed', 'false');
    const title =
      'Нагрузка и обед применяются кнопкой «Применить»: день пересчитывается заново, события дня сбрасываются, часы встают на начало дня';
    expect(workload()).toHaveAttribute('title', title);
    expect(lunch()).toHaveAttribute('title', title);
  });

  it('shows a day with lunch', () => {
    render(<MetricsStrip />);
    expect(workload().value).toBe('1');
    expect(lunch()).toHaveTextContent('с обедом');
    expect(lunch()).toHaveAttribute('aria-pressed', 'true');
  });

  it('rebuilds the day with the chosen workload level and the lunch of the session only on «Применить»', async () => {
    vi.mocked(api.buildPlan).mockResolvedValue(makePlanningState({ workload_level: 2, lunch_enabled: true, version: 5 }));
    vi.mocked(api.moveCursor).mockResolvedValue(makePlanningState({ workload_level: 2, lunch_enabled: true, version: 6, cursor: '09:00' }));
    render(<MetricsStrip />);
    expect(queryApply()).not.toBeInTheDocument();

    fireEvent.change(workload(), { target: { value: '2' } });
    expect(api.buildPlan).not.toHaveBeenCalled();
    expect(workload().value).toBe('2');

    fireEvent.click(apply());
    await waitFor(() => expect(useAppStore.getState().state?.version).toBe(6));
    expect(api.buildPlan).toHaveBeenCalledWith('d_test', { workload_level: 2, lunch: true });
    expect(useAppStore.getState().workloadLevel).toBe(2);
    expect(workload().value).toBe('2');
  });

  it('rebuilds the day with the flipped lunch and the workload level of the session', async () => {
    useAppStore.getState().setPlanningState(makePlanningState({ workload_level: 0, lunch_enabled: true }));
    vi.mocked(api.buildPlan).mockResolvedValue(makePlanningState({ workload_level: 0, lunch_enabled: false, version: 5 }));
    vi.mocked(api.moveCursor).mockResolvedValue(makePlanningState({ workload_level: 0, lunch_enabled: false, version: 6, cursor: '09:00' }));
    render(<MetricsStrip />);

    fireEvent.click(lunch());
    expect(api.buildPlan).not.toHaveBeenCalled();
    expect(lunch()).toHaveTextContent('без обеда');

    fireEvent.click(apply());
    await waitFor(() => expect(useAppStore.getState().state?.version).toBe(6));
    expect(api.buildPlan).toHaveBeenCalledWith('d_test', { workload_level: 0, lunch: false });
    expect(lunch()).toHaveTextContent('без обеда');
    expect(queryApply()).not.toBeInTheDocument();
  });

  it('changes the workload level and the lunch together with one rebuild', async () => {
    vi.mocked(api.buildPlan).mockResolvedValue(makePlanningState({ workload_level: 0, lunch_enabled: false, version: 5 }));
    vi.mocked(api.moveCursor).mockResolvedValue(makePlanningState({ workload_level: 0, lunch_enabled: false, version: 6, cursor: '09:00' }));
    render(<MetricsStrip />);

    fireEvent.change(workload(), { target: { value: '0' } });
    fireEvent.click(lunch());
    expect(api.buildPlan).not.toHaveBeenCalled();

    fireEvent.click(apply());
    await waitFor(() => expect(useAppStore.getState().state?.version).toBe(6));
    expect(vi.mocked(api.buildPlan).mock.calls).toEqual([['d_test', { workload_level: 0, lunch: false }]]);
    expect(workload().value).toBe('0');
    expect(lunch()).toHaveTextContent('без обеда');
  });

  it('«Отмена» returns the switches to the day on screen without a rebuild', () => {
    render(<MetricsStrip />);
    fireEvent.change(workload(), { target: { value: '2' } });
    fireEvent.click(lunch());

    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));

    expect(workload().value).toBe('1');
    expect(lunch()).toHaveTextContent('с обедом');
    expect(queryApply()).not.toBeInTheDocument();
    expect(api.buildPlan).not.toHaveBeenCalled();
  });

  it('does nothing when the level already on screen is chosen again', () => {
    useAppStore.getState().setPlanningState(makePlanningState({ workload_level: 2, lunch_enabled: false }));
    useAppStore.getState().setWorkloadLevel(0);
    render(<MetricsStrip />);

    fireEvent.change(workload(), { target: { value: '2' } });
    expect(queryApply()).not.toBeInTheDocument();
    // Выбор вернули к плану на экране: применять нечего.
    fireEvent.change(workload(), { target: { value: '0' } });
    fireEvent.change(workload(), { target: { value: '2' } });
    expect(queryApply()).not.toBeInTheDocument();
    expect(api.buildPlan).not.toHaveBeenCalled();
    expect(api.moveCursor).not.toHaveBeenCalled();
    expect(useAppStore.getState().workloadLevel).toBe(0);
  });

  it('puts the clock at the start of the day and moves the cursor there after a rebuild', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '14:10' });
    vi.mocked(api.buildPlan).mockResolvedValue(makePlanningState({ workload_level: 2, version: 5, cursor: '00:00' }));
    vi.mocked(api.moveCursor).mockResolvedValue(makePlanningState({ workload_level: 2, version: 6, cursor: '09:00' }));
    render(<MetricsStrip />);

    fireEvent.change(workload(), { target: { value: '2' } });
    fireEvent.click(apply());
    await waitFor(() => expect(useAppStore.getState().state?.version).toBe(6));
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', '09:00']]);
    expect(useAppStore.getState().clock).toBe('09:00');
    expect(screen.getByText('Сейчас 09:00')).toBeInTheDocument();
  });

  it('shows the time on the clock of the day as the current time', () => {
    useAppStore.setState({ clock: '14:25' });
    render(<MetricsStrip />);
    expect(screen.getByText('Сейчас 14:25')).toBeInTheDocument();
  });

  it.each([
    ['a calculation is running', { busy: true }],
    ['the clock plays', { playing: true }],
    ['the plan is being moved to the clock', { committing: true }],
  ])('blocks the workload level and the lunch while %s', (_, patch) => {
    useAppStore.setState(patch);
    render(<MetricsStrip />);
    expect(workload()).toBeDisabled();
    expect(lunch()).toBeDisabled();
  });

  it.each([
    ['the clock plays', { playing: true }],
    ['the plan is being moved to the clock', { committing: true }],
  ])('keeps «Другой файл» available while %s', (_, patch) => {
    useAppStore.setState(patch);
    render(<MetricsStrip />);
    expect(screen.getByRole('button', { name: 'Другой файл' })).toBeEnabled();
  });
});
