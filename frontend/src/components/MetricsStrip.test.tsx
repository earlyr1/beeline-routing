import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../store/useAppStore';
import { makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { MetricsStrip } from './MetricsStrip';

beforeEach(() => {
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
});
