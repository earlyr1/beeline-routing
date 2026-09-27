import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, getExplanation: vi.fn(), addTimelineEvent: vi.fn(), moveCursor: vi.fn() };
});

import * as api from '../api/client';
import { cancelEvent, delayEvent, reassignEvent } from '../lib/events';
import { useAppStore } from '../store/useAppStore';
import { makeAsapState, makeDataUrgentState, makeExplanation, makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { EngineerDelayDialog } from './events/EngineerDelayDialog';
import { ExplanationCard } from './ExplanationCard';

const card = () => screen.getByRole('region', { name: 'Объяснение по заявке' });
const headerActions = () => within(card().querySelector('.explanation__actions') as HTMLElement).getAllByRole('button');
const delayButton = () => within(card()).queryByRole('button', { name: 'Задержка бригады' });
const valueOf = (label: string) => (screen.getByLabelText(label) as HTMLInputElement).value;

beforeEach(() => {
  vi.resetAllMocks();
  resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104' });
});

describe('ExplanationCard', () => {
  it('loads the explanation and shows only the short verdict with «Почему?»', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    render(<ExplanationCard />);
    expect(await screen.findByText(/Назначена Бригада Арташкин/)).toBeInTheDocument();
    expect(api.getExplanation).toHaveBeenCalledWith('d_test', '50104');
    expect(screen.getByText(/окно 14:00–16:00 · 45 мин · Локальные работы/)).toBeInTheDocument();
    expect(screen.getByText(/приезд 13:35, начало 14:00/)).toBeInTheDocument();
    // Проверки, факторы и другие инженеры живут в панели «Почему», а не в карточке.
    expect(screen.queryByText('Временное окно')).not.toBeInTheDocument();
    expect(screen.queryByText('Не нужен дополнительный инженер')).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();

    const why = within(card()).getByRole('button', { name: 'Почему?' });
    expect(why).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(why);
    expect(useAppStore.getState().whyOpen).toBe(true);
    expect(why).toHaveAttribute('aria-pressed', 'true');
  });

  it('names an urgent request of the day with the URG- prefix but asks the server by its raw number', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makeDataUrgentState(), selectedRequestId: '50104' });
    render(<ExplanationCard />);
    expect(await screen.findByRole('heading', { name: 'Заявка URG-50104' })).toBeInTheDocument();
    expect(api.getExplanation).toHaveBeenCalledWith('d_test', '50104');
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

  it('cancels the request from the header next to «Изменить» at the time on the clock, through the undo notice', async () => {
    const cancelRequest = vi.fn();
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', clock: '13:30', cancelRequest });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    // В 13:30 бригада в пути к 50104: рядом стоит и «Задержка бригады».
    expect(headerActions().map((button) => button.textContent)).toEqual(['Изменить', 'Отменить', 'Задержка бригады', '✕']);
    const cancel = screen.getByRole('button', { name: 'Отменить' });
    expect(cancel).toBeEnabled();
    expect(cancel).not.toHaveAttribute('title');
    fireEvent.click(cancel);
    expect(cancelRequest).toHaveBeenCalledWith(cancelEvent('50104', '13:30'));
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
  });

  it('offers neither «Отменить» nor «Вернуть» for a cancelled request: restoring is not in the interface', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation({ request_id: '10135', status: 'cancelled', visit: null }));
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '10135', clock: '12:00' });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(headerActions().map((button) => button.textContent)).toEqual(['Изменить', '✕']);
    expect(screen.queryByRole('button', { name: 'Отменить' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Вернуть' })).not.toBeInTheDocument();
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

  it('помечает в шапке карточки заявку, к которой нужно везти оборудование', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation({ request_id: '74198' }));
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '74198' });
    const view = render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    const badge = within(card()).getByText('Оборудование');
    expect(badge).toHaveClass('badge', 'badge--equipment');
    expect(badge).toHaveAttribute('title', 'Нужно привезти оборудование: роутер, приставка или колонка');
    // Метка стоит в той же строке, что окно и навык.
    expect(badge.closest('p')).toHaveTextContent('окно 10:00–12:00 · 60 мин · Работы на подключение и дозаказы');
    view.unmount();

    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104' });
    render(<ExplanationCard />);
    await screen.findByText(/окно 14:00–16:00/);
    expect(within(card()).queryByText('Оборудование')).not.toBeInTheDocument();
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

  it('shows the brigade of the request under its details and reassigns it at the time on the clock', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', clock: '13:30', applyEvent });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    const picker = within(card()).getByRole('button', { name: 'Бригада: Бригада Арташкин' });
    // Строка выбора бригады стоит сразу после описания заявки.
    expect(card().querySelector('.explanation__head')?.nextElementSibling).toHaveClass('brigade-picker');
    fireEvent.click(picker);
    fireEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: /Бригада Белузин/ }));
    expect(applyEvent).toHaveBeenCalledWith(reassignEvent('50104', 'E02', '13:30'));
  });

  // 50104 у Арташкина: выезд 13:00, приезд 13:35, работа 14:00–14:45.
  it.each([
    ['the brigade is on the way to it', '13:30', true],
    ['the work is in progress', '14:10', true],
    ['the brigade has not left yet', '12:30', false],
    ['the brigade waits at the client', '13:40', false],
    ['the work is done', '14:45', false],
  ])('shows «Задержка бригады» only while the visit is under way: %s', async (_, clock, shown) => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', clock });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    if (shown) {
      expect(delayButton()).toBeEnabled();
      expect(delayButton()).toHaveClass('btn', 'btn-small');
    } else {
      expect(delayButton()).not.toBeInTheDocument();
    }
  });

  it('has no «Задержка бригады» for a request without a brigade', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '18754', clock: '18:30' });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(delayButton()).not.toBeInTheDocument();
  });

  it('locks «Задержка бригады» while replanning, like its neighbours, and for a brigade that is no longer available', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', clock: '14:10', busy: true });
    const view = render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(delayButton()).toBeDisabled();
    view.unmount();

    // URG-001 у Белузина в работе 13:05–14:05, а сам Белузин с 13:50 недоступен.
    const state = makePlanningState();
    const engineers = state.engineers.map((engineer) => (engineer.id === 'E02' ? { ...engineer, available: false, unavailable_from: '13:50' } : engineer));
    resetStore({ datasetId: 'd_test', state: { ...state, engineers }, selectedRequestId: 'URG-001', clock: '13:30' });
    render(<ExplanationCard />);
    await screen.findByText(/Назначена Бригада Арташкин/);
    expect(delayButton()).toBeDisabled();
    expect(delayButton()).toHaveAttribute('title', 'Инженер недоступен, задержку поставить нельзя');
  });

  it.each([
    ['from the planned end of the work in progress', '14:10', '14:45'],
    ['from the clock while the brigade is on the way', '13:30', '13:30'],
  ])('opens the delay dialog with the brigade of the visit, %s', async (_, clock, time) => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', clock });
    render(
      <>
        <ExplanationCard />
        <EngineerDelayDialog />
      </>,
    );
    await screen.findByText(/Назначена Бригада Арташкин/);
    fireEvent.click(delayButton() as HTMLElement);
    expect(screen.getByRole('dialog', { name: 'Задержка инженера' })).toBeInTheDocument();
    expect([valueOf('Инженер'), valueOf('На сколько минут'), valueOf('Задержка с')]).toEqual(['E01', '30', time]);
    // Время не раньше часов: задержка не уходит в прошлое.
    expect(valueOf('Задержка с') >= clock).toBe(true);
  });

  it('sends the usual delay event with the brigade and the planned end of the visit', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    vi.mocked(api.moveCursor).mockResolvedValue(makePlanningState({ cursor: '14:10', version: 5 }));
    vi.mocked(api.addTimelineEvent).mockResolvedValue(makePlanningState({ cursor: '14:10', version: 6 }));
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104', clock: '14:10' });
    render(
      <>
        <ExplanationCard />
        <EngineerDelayDialog />
      </>,
    );
    await screen.findByText(/Назначена Бригада Арташкин/);
    fireEvent.click(delayButton() as HTMLElement);
    fireEvent.click(screen.getByRole('button', { name: '60 мин' }));
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    await waitFor(() => expect(useAppStore.getState().delayDialogOpen).toBe(false));
    // Обычное событие задержки: на шкалу дня ко времени конца визита, дальше — общий поток событий и правило окна.
    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', delayEvent('E01', 60, '14:45'), undefined);
    expect(useAppStore.getState().state?.version).toBe(6);
  });

  it('renders nothing without a selection', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    const { container } = render(<ExplanationCard />);
    expect(container).toBeEmptyDOMElement();
    expect(api.getExplanation).not.toHaveBeenCalled();
  });
});
