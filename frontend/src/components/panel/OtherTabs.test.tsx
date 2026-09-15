import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { ComparisonTab } from './ComparisonTab';
import { PANEL_TABS } from './tabs';
import { TimelineTab } from './TimelineTab';
import { UnassignedTab } from './UnassignedTab';

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('panel tabs', () => {
  it('registers the base tabs and the proposals tab in order with an unassigned badge', () => {
    expect(PANEL_TABS.map((tab) => tab.id)).toEqual(['requests', 'timeline', 'unassigned', 'comparison', 'proposals']);
    const unassigned = PANEL_TABS.find((tab) => tab.id === 'unassigned');
    expect(unassigned?.badge?.(useAppStore.getState())).toBe(1);
  });

  it('UnassignedTab shows the reason and selects the request', () => {
    render(<UnassignedTab />);
    expect(screen.getByText('Не помещается в окно или смену')).toBeInTheDocument();
    expect(screen.getByText(/даже без других заявок Бригада Белузин/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('18754'));
    expect(useAppStore.getState().selectedRequestId).toBe('18754');
  });

  it('UnassignedTab shows an empty message when everything is assigned', () => {
    const state = makePlanningState();
    resetStore({ state: { ...state, plan: { ...state.plan, unassigned: [] } } });
    render(<UnassignedTab />);
    expect(screen.getByText('Все заявки распределены.')).toBeInTheDocument();
  });

  it('ComparisonTab shows three plans and the delta to baseline', () => {
    render(<ComparisonTab />);
    const engineers = screen.getByText('Задействовано инженеров').closest('tr') as HTMLElement;
    expect(engineers).toHaveTextContent('Задействовано инженеров223');
    expect(screen.getByText('−1')).toHaveClass('delta--better');
    expect(screen.getByText('Бригада Комарь').closest('tr')).toHaveTextContent('9,8 км');
  });

  it('TimelineTab renders visit bars and selects a request', () => {
    render(<TimelineTab />);
    fireEvent.click(screen.getByRole('button', { name: 'Заявка 50104 14:00–14:45' }));
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
    expect(screen.getByText(/текущее время 13:00/)).toBeInTheDocument();
    expect(screen.getByText('09:00')).toBeInTheDocument();
  });
});
