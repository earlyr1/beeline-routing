import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, getExplanation: vi.fn() };
});

import * as api from '../../api/client';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { RightPanel } from './RightPanel';

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getExplanation).mockReturnValue(new Promise(() => undefined));
  resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedEngineerId: 'E01' });
});

describe('RightPanel', () => {
  it('shows the route card above the tabs when an engineer is selected', () => {
    render(<RightPanel />);
    const card = screen.getByRole('region', { name: 'Бригада' });
    const position = card.compareDocumentPosition(screen.getByRole('tablist'));
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('ставит число заявок без бригады на «Заявки» и открывает их вместо вкладки неназначенных, которой больше нет', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), activeTab: 'unassigned' });
    render(<RightPanel />);
    const requests = screen.getByRole('tab', { name: /^Заявки/ });
    expect(requests).toHaveAttribute('aria-selected', 'true');
    expect(within(requests).getByText('1')).toHaveAttribute('title', 'Заявки без исполнителя');
    expect(screen.getByRole('button', { name: /^Без исполнителя/ })).toBeInTheDocument();
  });

  it('показывает заявки без бригады под открытой страницей бригады, а карточка такой заявки возвращает на бригаду', () => {
    render(<RightPanel />);
    fireEvent.click(screen.getByRole('button', { name: /^Без исполнителя/ }));
    // Страница бригады с таймлайном остаётся, а под ней — то, что можно ей отдать.
    expect(screen.getByRole('region', { name: 'Бригада' })).toBeInTheDocument();
    const panel = screen.getByRole('tabpanel');
    expect(within(panel).getAllByRole('listitem').map((item) => item.querySelector('strong')?.textContent)).toEqual(['18754']);

    fireEvent.click(within(panel).getByText('18754'));
    expect(screen.getByRole('region', { name: 'Объяснение по заявке' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '← Бригада Арташкин' }));
    expect(screen.getByRole('region', { name: 'Бригада' })).toBeInTheDocument();
    expect(useAppStore.getState()).toMatchObject({ selectedEngineerId: 'E01', selectedRequestId: null, unassignedOnly: true });
  });

  it('gives way to the request explanation while a request card is open', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedEngineerId: 'E01', selectedRequestId: '50104' });
    render(<RightPanel />);
    expect(screen.getByRole('region', { name: 'Объяснение по заявке' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Бригада' })).not.toBeInTheDocument();
  });
});
