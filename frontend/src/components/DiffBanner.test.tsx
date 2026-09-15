import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../store/useAppStore';
import { makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { DiffBanner } from './DiffBanner';

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('DiffBanner', () => {
  it('summarises the last event and metrics before and after', () => {
    render(<DiffBanner />);
    expect(screen.getByText('Срочная заявка URG-001 в 13:00')).toBeInTheDocument();
    expect(screen.getByText(/Новых назначений: 1 · перенесено: 1 · снято: 0 · сдвиг\s+времени: 1/)).toBeInTheDocument();
    expect(screen.getByText(/пробег 31,7 км → 34,9 км · не назначено 1 → 1/)).toBeInTheDocument();
  });

  it('switches between the plan before and after the event', () => {
    render(<DiffBanner />);
    fireEvent.click(screen.getByRole('button', { name: 'До события' }));
    expect(useAppStore.getState().showPrevious).toBe(true);
    expect(screen.getByRole('button', { name: 'До события' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Скрыть' }));
    expect(useAppStore.getState().showPrevious).toBe(false);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('is hidden when there is no diff', () => {
    resetStore({ state: makePlanningState({ last_diff: null }) });
    const { container } = render(<DiffBanner />);
    expect(container).toBeEmptyDOMElement();
  });
});
