import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, addTimelineEvent: vi.fn(), deleteTimelineEvent: vi.fn(), moveCursor: vi.fn() };
});

import * as api from '../../api/client';
import type { PlanningState } from '../../api/types';
import { cancelEvent } from '../../lib/events';
import { CANCEL_UNDO_MS, useAppStore } from '../../store/useAppStore';
import { makeConfig, makePlanningState, makeTimeline } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { CommunicationsTab } from './CommunicationsTab';

const rows = () => screen.getAllByRole('listitem');
const rowOf = (label: string) => rows().find((item) => within(item).queryByText(label) !== null)!;

/**
 * День фикстуры, в котором есть все три повода для звонка: 86160 не успеваем в окно (визит уехал на 15:10),
 * 18754 осталась без визита, а окно 46393 диспетчер подвинул на 16:00–18:00, и визит переехал в него.
 */
function callingDay(): PlanningState {
  const state = makePlanningState();
  const moved: Record<string, { start: string; end: string }> = {
    '86160': { start: '15:10', end: '16:10' },
    '46393': { start: '16:30', end: '17:15' },
  };
  return {
    ...state,
    requests: state.requests.map((request) =>
      request.id === '46393' ? { ...request, window_start: '16:00', window_end: '18:00' } : request,
    ),
    plan: {
      ...state.plan,
      routes: state.plan.routes.map((route) => ({
        ...route,
        visits: route.visits.map((visit) => ({ ...visit, ...moved[visit.request_id] })),
      })),
    },
  };
}

afterEach(() => {
  vi.useRealTimers();
});

beforeEach(() => {
  vi.mocked(api.addTimelineEvent).mockReset();
  vi.mocked(api.deleteTimelineEvent).mockReset();
  resetStore({ datasetId: 'd_test', state: callingDay(), clock: '13:00', config: makeConfig() });
  // Звонок — событие шкалы: сервер применяет его и возвращает договорённость вместе с планом, как настоящий.
  vi.mocked(api.addTimelineEvent).mockImplementation(async (_dataset, event) => {
    const state = useAppStore.getState().state!;
    const agreed = { window: event.agreed_window ?? null, entry_id: 'tl_7' };
    return { ...state, agreed: { ...(state.agreed ?? {}), [event.request_id!]: agreed } };
  });
});

describe('CommunicationsTab', () => {
  it('shows who to call: the number, the address, the broken window and the brigade', () => {
    render(<CommunicationsTab />);
    expect(rows()).toHaveLength(3);

    const lost = rowOf('18754');
    expect(lost).toHaveClass('call--red');
    expect(within(lost).getByText('ул.1-я Новокузьминская, д. 16 к 1')).toBeInTheDocument();
    expect(within(lost).getByText('Сегодня не приедем')).toBeInTheDocument();
    expect(within(lost).getByText('сегодня не приедем, обещали окно 18:00–20:00')).toBeInTheDocument();

    // В окно клиента не попадаем: строка красная, бригада в ней — справка, а не повод для звонка.
    const late = rowOf('86160');
    expect(late).toHaveClass('call--red');
    expect(within(late).getByText('Вне окна')).toBeInTheDocument();
    expect(
      within(late).getByText('не попадаем в окно 12:00–14:00 — назовите окно 14:00–16:00 · Бригада Арташкин'),
    ).toBeInTheDocument();

    // Окно подвинул сам диспетчер: клиенту нужно назвать новое.
    const window = rowOf('46393');
    expect(window).toHaveClass('call--yellow');
    expect(within(window).getByText('Новое окно')).toBeInTheDocument();
    expect(within(window).getByText('окно было 15:00–17:00 → стало 16:00–18:00 · Бригада Арташкин')).toBeInTheDocument();
  });

  it('says nothing about a visit that moved inside the window of the client or went to another brigade', () => {
    // 50104 уехала от Белузина к Арташкину, 46393 переехала с 15:00 на 15:10 — оба внутри окон клиентов.
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00', config: makeConfig() });
    render(<CommunicationsTab />);
    expect(rows()).toHaveLength(1);
    expect(rowOf('18754')).toBeInTheDocument();
  });

  it('marks a row as agreed with an event on the time bar and moves it to the block below', async () => {
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: '✓ Согласовано' }));

    const window = { start: '16:00', end: '18:00', asap: false };
    // Звонок уходит на шкалу событием «Коммуникация» во время часов, без стратегии: окно выбора решит сервер.
    await waitFor(() =>
      expect(api.addTimelineEvent).toHaveBeenCalledWith(
        'd_test',
        { type: 'client_agreed', time: '13:00', request: null, request_id: '46393', engineer_id: null, agreed_window: window },
        undefined,
      ),
    );
    await waitFor(() => expect(useAppStore.getState().agreed).toEqual({ '46393': { window, entry_id: 'tl_7' } }));
    const agreed = rowOf('46393');
    expect(agreed).toHaveClass('call--agreed');
    expect(within(agreed).getByText('договорились на окно 16:00–18:00')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Согласовано' })).toBeInTheDocument();
  });

  it('moves the row down at once, before the server has planned the call', async () => {
    let refuse: (error: Error) => void = () => undefined;
    vi.mocked(api.addTimelineEvent).mockImplementation(() => new Promise((_resolve, reject) => (refuse = reject)));
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: '✓ Согласовано' }));

    // Диспетчер уже положил трубку: строка внизу, но снять отметку пока нечем — события на шкале ещё нет.
    const sending = rowOf('46393');
    expect(sending).toHaveClass('call--agreed');
    expect(within(sending).getByRole('button', { name: 'Снять отметку' })).toBeDisabled();

    // Сервер звонок не принял: строка возвращается в список звонков, а причина — в сообщении об ошибке.
    await waitFor(() => expect(api.addTimelineEvent).toHaveBeenCalled());
    await act(async () => refuse(new api.ApiError(422, 'Заявка 46393 уже в работе с 13:00, перенести её нельзя.')));
    await waitFor(() => expect(rowOf('46393')).toHaveClass('call--yellow'));
    expect(useAppStore.getState().error).toBe('Заявка 46393 уже в работе с 13:00, перенести её нельзя.');
  });

  it('remembers «сегодня не приедем» for a client who is left without a visit', async () => {
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('18754')).getByRole('button', { name: '✓ Согласовано' }));

    await waitFor(() =>
      expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', expect.objectContaining({ request_id: '18754', agreed_window: null }), undefined),
    );
    await waitFor(() => expect(useAppStore.getState().agreed).toEqual({ '18754': { window: null, entry_id: 'tl_7' } }));
    expect(within(rowOf('18754')).getByText('сказали, что сегодня не приедем')).toBeInTheDocument();
  });

  it('takes the mark back by deleting its event from the time bar', async () => {
    const state = callingDay();
    const agreed = { '46393': { window: { start: '16:00', end: '18:00', asap: false }, entry_id: 'tl_7' } };
    resetStore({ datasetId: 'd_test', state: { ...state, agreed }, agreed, clock: '13:00', config: makeConfig() });
    vi.mocked(api.deleteTimelineEvent).mockResolvedValue(state);
    render(<CommunicationsTab />);

    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: 'Снять отметку' }));
    await waitFor(() => expect(api.deleteTimelineEvent).toHaveBeenCalledWith('d_test', 'tl_7'));
    await waitFor(() => expect(rowOf('46393')).toHaveClass('call--yellow'));
  });

  it('cancels the request of a client who refused, with or without replanning the rest of the day', () => {
    const cancelRequest = vi.fn();
    useAppStore.setState({ cancelRequest });
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: '✕ Клиент отказался' }));

    // Выбор разворачивается прямо в строке: уходить с вкладки не нужно.
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: 'Отменить, маршруты не трогать' }));
    expect(cancelRequest).toHaveBeenCalledWith(cancelEvent('46393', '13:00'), 'keep');
    expect(screen.queryByRole('button', { name: 'Отменить, маршруты не трогать' })).toBeNull();

    fireEvent.click(within(rowOf('18754')).getByRole('button', { name: '✕ Клиент отказался' }));
    fireEvent.click(within(rowOf('18754')).getByRole('button', { name: 'Отменить и пересчитать остаток дня' }));
    expect(cancelRequest).toHaveBeenLastCalledWith(cancelEvent('18754', '13:00'), 'optimal');
  });

  it('carries «маршруты не трогать» through the undo notice into the event that reaches the server', async () => {
    vi.useFakeTimers();
    const state = { ...useAppStore.getState().state!, cursor: '13:00' };
    useAppStore.setState({ state });
    vi.mocked(api.addTimelineEvent).mockResolvedValue(state);
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: '✕ Клиент отказался' }));
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: 'Отменить, маршруты не трогать' }));
    // Пока идёт уведомление, на сервер ничего не ушло, а выбор диспетчера ждёт вместе с событием.
    expect(api.addTimelineEvent).not.toHaveBeenCalled();
    expect(useAppStore.getState().pendingCancel).toMatchObject({ event: cancelEvent('46393', '13:00'), variant: 'keep' });

    await act(() => vi.advanceTimersByTimeAsync(CANCEL_UNDO_MS));
    expect(api.addTimelineEvent).toHaveBeenCalledTimes(1);
    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', cancelEvent('46393', '13:00'), 'keep');
  });

  it('does not offer to cancel or to agree about a request that is already being worked on', () => {
    // 86160 начали в 12:00 и закрепили: отменять её поздно, хотя в окно клиента бригада уже не успевает. И звонок
    // окна ей не поменяет — сервер такое событие не примет, клиенту говорят, что бригада у него.
    render(<CommunicationsTab />);
    expect(within(rowOf('86160')).getByRole('button', { name: '✕ Клиент отказался' })).toBeDisabled();
    const agree = within(rowOf('86160')).getByRole('button', { name: '✓ Согласовано' });
    expect(agree).toBeDisabled();
    expect(agree).toHaveAttribute('title', 'Работа уже началась, отметить звонок нельзя');
    expect(within(rowOf('46393')).getByRole('button', { name: '✓ Согласовано' })).toBeEnabled();
  });

  it('opens the request of a clicked row', () => {
    render(<CommunicationsTab />);
    fireEvent.click(rowOf('46393'));
    expect(useAppStore.getState().selectedRequestId).toBe('46393');
  });

  it('lets go of the visits of the day that are already over', () => {
    // К девяти вечера работы плана закончились: звонить остаётся только тому, к кому сегодня не приедут.
    resetStore({ datasetId: 'd_test', state: callingDay(), clock: '21:00', config: makeConfig() });
    render(<CommunicationsTab />);
    expect(rows()).toHaveLength(1);
    expect(within(rowOf('18754')).getByText('сегодня не приедем, обещали окно 18:00–20:00')).toBeInTheDocument();
  });

  it('warns that the clock has not reached the events on the time bar yet', () => {
    resetStore({ datasetId: 'd_test', clock: '13:00', state: { ...callingDay(), timeline: makeTimeline() }, config: makeConfig() });
    render(<CommunicationsTab />);
    expect(screen.getByText(/События впереди часов|события впереди часов/)).toBeInTheDocument();
  });

  it('says what each of the two cancellations costs before the dispatcher picks one', () => {
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: '✕ Клиент отказался' }));
    const row = rowOf('46393');
    expect(within(row).getByText('Остаток дня пересчитаем: визиты других клиентов могут переехать')).toBeInTheDocument();
    expect(within(row).getByText('Времена остальных визитов останутся как есть, у бригады появится окно')).toBeInTheDocument();
  });

  it('says there is nobody to call when the plan keeps the windows the clients know', () => {
    const state = makePlanningState();
    // Без 18754, которая осталась без визита: остальным клиентам обещанные окна выполняются.
    resetStore({
      datasetId: 'd_test',
      state: { ...state, requests: state.requests.filter((request) => request.id !== '18754') },
      config: makeConfig(),
    });
    render(<CommunicationsTab />);
    expect(screen.getByText('Звонить некому: обещанные клиентам окна выполняются.')).toBeInTheDocument();
  });
});
