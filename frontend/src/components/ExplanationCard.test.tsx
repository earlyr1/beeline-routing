import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, getExplanation: vi.fn() };
});

import * as api from '../api/client';
import { useAppStore } from '../store/useAppStore';
import { makeExplanation, makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { ExplanationCard } from './ExplanationCard';

beforeEach(() => {
  vi.resetAllMocks();
  resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104' });
});

describe('ExplanationCard', () => {
  it('loads and shows the explanation for the selected request', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    render(<ExplanationCard />);
    expect(await screen.findByText(/Назначена Бригада Арташкин/)).toBeInTheDocument();
    expect(api.getExplanation).toHaveBeenCalledWith('d_test', '50104');
    expect(screen.getByText('Временное окно')).toBeInTheDocument();
    expect(screen.getByText('Не нужен дополнительный инженер')).toBeInTheDocument();
    expect(screen.getByText('Бригада Белузин').closest('tr')).toHaveTextContent('+2,1 км');
    expect(screen.getByText(/окно 14:00–16:00 · 45 мин · Локальные работы/)).toBeInTheDocument();
  });

  it('shows the error and closes', async () => {
    vi.mocked(api.getExplanation).mockRejectedValue(new api.ApiError(404, 'Заявка не найдена'));
    render(<ExplanationCard />);
    expect(await screen.findByText('Заявка не найдена')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть объяснение' }));
    expect(useAppStore.getState().selectedRequestId).toBeNull();
  });

  it('renders nothing without a selection', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    const { container } = render(<ExplanationCard />);
    expect(container).toBeEmptyDOMElement();
    expect(api.getExplanation).not.toHaveBeenCalled();
  });
});
