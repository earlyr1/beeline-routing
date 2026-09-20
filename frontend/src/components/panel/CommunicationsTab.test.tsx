import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cancelEvent } from '../../lib/events';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState, makeTimeline } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { CommunicationsTab } from './CommunicationsTab';

const rows = () => screen.getAllByRole('listitem');
const rowOf = (label: string) => rows().find((item) => within(item).queryByText(label) !== null)!;

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
});

describe('CommunicationsTab', () => {
  it('shows who to call: the number, the address, the change and the brigade when it changed', () => {
    render(<CommunicationsTab />);
    expect(rows()).toHaveLength(4);

    const lost = rowOf('18754');
    expect(lost).toHaveClass('call--red');
    expect(within(lost).getByText('Сегодня не приедем')).toBeInTheDocument();
    expect(within(lost).getByText('ул.1-я Новокузьминская, д. 16 к 1')).toBeInTheDocument();
    expect(within(lost).getByText('было 18:00 → сегодня не приедем')).toBeInTheDocument();

    // Время прежнее, бригада другая: серая строка, но позвонить всё равно нужно.
    const moved = rowOf('50104');
    expect(moved).toHaveClass('call--grey');
    expect(within(moved).getByText('время прежнее, 14:00 · Бригада Белузин → Бригада Арташкин')).toBeInTheDocument();
    // Срочная заявка: клиенту ничего не обещали, с ним договариваются о времени.
    expect(within(rowOf('URG-001')).getByText('Договориться о времени')).toBeInTheDocument();
  });

  it('marks a row as agreed, moves it to the block below and remembers the time', () => {
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: '✓ Согласовано' }));

    expect(useAppStore.getState().agreed).toEqual({ '46393': { start: '15:10', engineer_id: 'E01', version: 4 } });
    const agreed = rowOf('46393');
    expect(agreed).toHaveClass('call--agreed');
    expect(within(agreed).getByText('договорились на 15:10')).toBeInTheDocument();
    expect(within(agreed).queryByRole('button')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Согласовано' })).toBeInTheDocument();
  });

  it('cancels the request of a client who refused, with or without replanning the rest of the day', () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('50104')).getByRole('button', { name: '✕ Клиент отказался' }));

    // Выбор разворачивается прямо в строке: уходить с вкладки не нужно.
    fireEvent.click(within(rowOf('50104')).getByRole('button', { name: 'Отменить, маршруты не трогать' }));
    expect(applyEvent).toHaveBeenCalledWith(cancelEvent('50104', '13:00'), 'keep');
    expect(screen.queryByRole('button', { name: 'Отменить, маршруты не трогать' })).toBeNull();

    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: '✕ Клиент отказался' }));
    fireEvent.click(within(rowOf('46393')).getByRole('button', { name: 'Отменить и пересчитать остаток дня' }));
    expect(applyEvent).toHaveBeenLastCalledWith(cancelEvent('46393', '13:00'), 'optimal');
  });

  it('does not offer to cancel a request that is already being worked on', () => {
    // 86160 начали в 12:00 и закрепили: отменять её поздно, а время клиенту не двигалось. Работа идёт до 13:00,
    // поэтому часы стоят на 12:30: законченный визит из списка уходит.
    const state = makePlanningState();
    resetStore({
      datasetId: 'd_test',
      clock: '12:30',
      state: { ...state, morning: state.morning.map((item) => (item.request_id === '86160' ? { ...item, start: '11:00' } : item)) },
    });
    render(<CommunicationsTab />);
    expect(within(rowOf('86160')).getByRole('button', { name: '✕ Клиент отказался' })).toBeDisabled();
    expect(within(rowOf('86160')).getByRole('button', { name: '✓ Согласовано' })).toBeEnabled();
  });

  it('opens the request of a clicked row', () => {
    render(<CommunicationsTab />);
    fireEvent.click(rowOf('50104'));
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
  });

  it('lets go of the visits of the day that are already over', () => {
    // К девяти вечера работы плана закончились: звонить остаётся только тому, к кому сегодня не приедут.
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '21:00' });
    render(<CommunicationsTab />);
    expect(rows()).toHaveLength(1);
    expect(within(rowOf('18754')).getByText('было 18:00 → сегодня не приедем')).toBeInTheDocument();
  });

  it('follows «До события» like the rest of the screen and does not let the dispatcher agree on a plan that is gone', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00', showPrevious: true });
    render(<CommunicationsTab />);
    // До срочной заявки план совпадал с утренним у всех, кроме 18754: её бригада стала недоступна.
    expect(rows()).toHaveLength(1);
    expect(within(rowOf('18754')).getByRole('button', { name: '✓ Согласовано' })).toBeDisabled();
  });

  it('warns that the clock has not reached the events on the time bar yet', () => {
    const state = makePlanningState();
    resetStore({ datasetId: 'd_test', clock: '13:00', state: { ...state, timeline: makeTimeline() } });
    render(<CommunicationsTab />);
    expect(screen.getByText(/События впереди часов|события впереди часов/)).toBeInTheDocument();
  });

  it('says what each of the two cancellations costs before the dispatcher picks one', () => {
    render(<CommunicationsTab />);
    fireEvent.click(within(rowOf('50104')).getByRole('button', { name: '✕ Клиент отказался' }));
    const row = rowOf('50104');
    expect(within(row).getByText('Остаток дня пересчитаем: визиты других клиентов могут переехать')).toBeInTheDocument();
    expect(within(row).getByText('Времена остальных визитов останутся как есть, у бригады появится окно')).toBeInTheDocument();
  });

  it('says there is nobody to call when the plan matches what the clients know', () => {
    const state = makePlanningState();
    resetStore({
      datasetId: 'd_test',
      // Утро совпадает с планом на 13:00: звонить некому.
      state: {
        ...state,
        requests: state.requests.filter((request) => request.id !== 'URG-001' && request.id !== '18754'),
        morning: state.plan.routes.flatMap((route) =>
          route.visits.map((visit) => ({ request_id: visit.request_id, engineer_id: route.engineer_id, start: visit.start })),
        ),
      },
    });
    render(<CommunicationsTab />);
    expect(screen.getByText('Звонить некому: клиенты знают то же, что в плане.')).toBeInTheDocument();
  });
});
