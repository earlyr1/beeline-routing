import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, getExplanation: vi.fn() };
});

import * as api from '../api/client';
import { cancelEvent, restoreEvent } from '../lib/events';
import { useAppStore } from '../store/useAppStore';
import { makeAsapState, makeExplanation, makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { ExplanationCard } from './ExplanationCard';

const card = () => screen.getByRole('region', { name: 'Объяснение по заявке' });
const headerActions = () => within(card().querySelector('.explanation__actions') as HTMLElement).getAllByRole('button');

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

  it('opens the request edit from the header', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    const edit = screen.getByRole('button', { name: 'Изменить' });
    expect(edit).toBeEnabled();
    fireEvent.click(edit);
    expect(useAppStore.getState().editingRequestId).toBe('50104');
  });

  it('cancels the request from the header next to «Изменить» at the time on the clock', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', clock: '13:30', applyEvent });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(headerActions().map((button) => button.textContent)).toEqual(['Изменить', 'Отменить', '✕']);
    const cancel = screen.getByRole('button', { name: 'Отменить' });
    expect(cancel).toBeEnabled();
    expect(cancel).not.toHaveAttribute('title');
    fireEvent.click(cancel);
    expect(applyEvent).toHaveBeenCalledWith(cancelEvent('50104', '13:30'));
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
  });

  it('restores a cancelled request from the header at the time on the clock, even before the time of the plan', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation({ request_id: '10135', status: 'cancelled', visit: null }));
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '10135', clock: '12:00', applyEvent });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(screen.queryByRole('button', { name: 'Отменить' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Вернуть' }));
    expect(applyEvent).toHaveBeenCalledWith(restoreEvent('10135', '12:00'));
  });

  it('treats a visit that started before the clock as started work, like the server', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', clock: '14:10' });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(screen.getByRole('button', { name: 'Изменить' })).toHaveAttribute('title', 'Работа уже началась, изменить нельзя');
    expect(screen.getByRole('button', { name: 'Отменить' })).toBeDisabled();
  });

  it.each([
    ['started work', { selectedRequestId: '74198' }],
    ['the plan before the event', { showPrevious: true }],
    ['an event being applied', { busy: true }],
  ])('disables the edit and the cancel for %s', async (_, patch) => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', ...patch });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(screen.getByRole('button', { name: 'Изменить' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Отменить' })).toBeDisabled();
  });

  it('explains why started work cannot be edited or cancelled', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '74198' });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(screen.getByRole('button', { name: 'Изменить' })).toHaveAttribute('title', 'Работа уже началась, изменить нельзя');
    expect(screen.getByRole('button', { name: 'Отменить' })).toHaveAttribute('title', 'Работа уже началась, отменить нельзя');
  });

  it('opens the brigade page from the engineer of the visit', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    render(<ExplanationCard />);
    const visit = (await screen.findByText(/приезд 13:35/)) as HTMLElement;
    fireEvent.click(within(visit).getByRole('button', { name: 'Бригада Арташкин' }));
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: null, selectedEngineerId: 'E01' });
  });

  it('opens the brigade page from the table of other engineers', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    render(<ExplanationCard />);
    const table = (await screen.findByRole('table')) as HTMLElement;
    fireEvent.click(within(table).getByRole('button', { name: 'Бригада Комарь' }));
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: null, selectedEngineerId: 'E03' });
  });

  it('links back to the brigade page the request card was opened from', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', selectedEngineerId: 'E02' });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    fireEvent.click(within(card()).getByRole('button', { name: '← Бригада Белузин' }));
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: null, selectedEngineerId: 'E02' });
  });

  it('has no link back without a selected brigade', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(within(card()).queryByRole('button', { name: /^←/ })).not.toBeInTheDocument();
  });

  it('shows «как можно скорее» instead of the window in the header', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation({ request_id: 'URG-002' }));
    resetStore({ datasetId: 'd_test', state: makeAsapState(), selectedRequestId: 'URG-002' });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(
      within(card()).getByText(/^ул\.Перовская, д\. 42 к 1 · как можно скорее с 13:00 · 60 мин · Аварийные работы · нужен транспорт/),
    ).toBeInTheDocument();
    expect(within(card()).queryByText(/· окно /)).not.toBeInTheDocument();
  });

  it('показывает статус заявки на время часов рядом с её номером', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', clock: '14:10' });
    const view = render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(within(card()).getByText('В работе')).toHaveClass('badge', 'badge--clock-working');
    view.unmount();

    // В 11:30 инженер ещё не выехал к 50104: статуса нет.
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', clock: '11:30' });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(within(card()).queryByText('В работе')).not.toBeInTheDocument();
  });

  it('renders nothing without a selection', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    const { container } = render(<ExplanationCard />);
    expect(container).toBeEmptyDOMElement();
    expect(api.getExplanation).not.toHaveBeenCalled();
  });
});
