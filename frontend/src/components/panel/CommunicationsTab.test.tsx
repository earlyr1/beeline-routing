import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, setAgreedWindow: vi.fn() };
});

import * as api from '../../api/client';
import type { AgreedWindow, PlanningState } from '../../api/types';
import { cancelEvent } from '../../lib/events';
import { useAppStore } from '../../store/useAppStore';
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

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: callingDay(), clock: '13:00', config: makeConfig() });
  // Отметку «Согласовано» помнит сервер: он возвращает её вместе с планом, как настоящий.
  vi.mocked(api.setAgreedWindow).mockImplementation(async (_dataset, requestId, window: AgreedWindow) => {
    const state = useAppStore.getState().state!;
    return { ...state, agreed: { ...(state.agreed ?? {}), [requestId]: window } };
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

  it('marks a row as agreed, moves it to the block below and remembers the window', async () => {
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: '✓ Согласовано' }));

    const mark = {
      window: { start: '16:00', end: '18:00', asap: false },
      request_window: { start: '16:00', end: '18:00', asap: false },
      version: 4,
    };
    // Окно уходит на сервер: его увидит и другая вкладка, и тот же день после перезапуска сервиса.
    await waitFor(() => expect(api.setAgreedWindow).toHaveBeenCalledWith('d_test', '46393', mark));
    expect(useAppStore.getState().agreed).toEqual({ '46393': mark });
    const agreed = rowOf('46393');
    expect(agreed).toHaveClass('call--agreed');
    expect(within(agreed).getByText('договорились на окно 16:00–18:00')).toBeInTheDocument();
    expect(within(agreed).queryByRole('button')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Согласовано' })).toBeInTheDocument();
  });

  it('remembers «сегодня не приедем» for a client who is left without a visit', async () => {
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('18754')).getByRole('button', { name: '✓ Согласовано' }));

    await waitFor(() => expect(api.setAgreedWindow).toHaveBeenCalled());
    expect(useAppStore.getState().agreed).toEqual({
      '18754': { window: null, request_window: { start: '18:00', end: '20:00', asap: false }, version: 4 },
    });
    expect(within(rowOf('18754')).getByText('сказали, что сегодня не приедем')).toBeInTheDocument();
  });

  it('cancels the request of a client who refused, with or without replanning the rest of the day', () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: '✕ Клиент отказался' }));

    // Выбор разворачивается прямо в строке: уходить с вкладки не нужно.
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: 'Отменить, маршруты не трогать' }));
    expect(applyEvent).toHaveBeenCalledWith(cancelEvent('46393', '13:00'), 'keep');
    expect(screen.queryByRole('button', { name: 'Отменить, маршруты не трогать' })).toBeNull();

    fireEvent.click(within(rowOf('18754')).getByRole('button', { name: '✕ Клиент отказался' }));
    fireEvent.click(within(rowOf('18754')).getByRole('button', { name: 'Отменить и пересчитать остаток дня' }));
    expect(applyEvent).toHaveBeenLastCalledWith(cancelEvent('18754', '13:00'), 'optimal');
  });

  it('does not offer to cancel a request that is already being worked on', () => {
    // 86160 начали в 12:00 и закрепили: отменять её поздно, хотя в окно клиента бригада уже не успевает.
    render(<CommunicationsTab />);
    expect(within(rowOf('86160')).getByRole('button', { name: '✕ Клиент отказался' })).toBeDisabled();
    expect(within(rowOf('86160')).getByRole('button', { name: '✓ Согласовано' })).toBeEnabled();
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
