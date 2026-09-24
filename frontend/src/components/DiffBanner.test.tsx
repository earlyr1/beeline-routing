import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../store/useAppStore';
import {
  makeDelayedState,
  makeDelayEvent,
  makePlanningState,
  makeRequestUpdateEvent,
  makeTimelineItem,
  makeTransportChangeEvent,
} from '../test/fixtures';
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

  it('titles a transport change with the old and the new transport', () => {
    const state = makePlanningState();
    const engineers = state.engineers.map((engineer) => (engineer.id === 'E01' ? { ...engineer, transport: 'bike' as const } : engineer));
    const events = [...state.events, { id: 'ev_4', event: makeTransportChangeEvent(), version: 5 }];
    resetStore({ state: { ...state, version: 5, engineers, events } });
    render(<DiffBanner />);
    expect(screen.getByText('Смена транспорта: Бригада Арташкин, Автомобиль → Велосипед с 13:30')).toBeInTheDocument();
  });

  it('titles a request update with what changed in the request', () => {
    const state = makePlanningState();
    const event = makeRequestUpdateEvent();
    const requests = state.requests.map((request) => (request.id === '50104' && event.request ? event.request : request));
    const events = [...state.events, { id: 'ev_4', event, version: 5 }];
    resetStore({ state: { ...state, version: 5, requests, events } });
    render(<DiffBanner />);
    expect(screen.getByText('Изменена заявка 50104 с 13:30: окно 14:00–16:00 → 15:00–17:00, длительность 45 → 60 мин')).toBeInTheDocument();
  });

  it('forecasts the lateness of a delay without replanning with the late visits in the title', () => {
    resetStore({ state: makeDelayedState() });
    render(<DiffBanner />);
    expect(screen.getByText('Задержка: Бригада Арташкин на 150 мин с 13:30')).toBeInTheDocument();
    const line = screen.getByText('Без перепланирования опоздали бы к 2 клиентам на 35–45 мин');
    expect(line).toHaveAttribute('title', '50104: план 14:00, прогноз 16:35, +35 мин\n46393: план 15:10, прогноз 17:45, +45 мин');
  });

  it('speaks of the lateness as a fact when the delay went with «ничего не менять»', () => {
    // Пересчёт ничего не спасал: сервер провёл задержку с «Ничего не менять» без окна, и опоздания остались в плане.
    const state = makeDelayedState();
    const timeline = [makeTimelineItem({ id: 'tl_4', event: makeDelayEvent(), status: 'applied', variant: 'keep', variant_auto: true })];
    resetStore({ state: { ...state, timeline } });
    render(<DiffBanner />);
    expect(screen.getByText('Опоздаем к 2 клиентам на 35–45 мин')).toBeInTheDocument();
    expect(screen.queryByText(/Без перепланирования/)).not.toBeInTheDocument();
  });

  it('renders every forecast line on its own and says when nobody would be late', () => {
    resetStore({ state: makeDelayedState({ overtime_without_replan_min: 20 }) });
    const first = render(<DiffBanner />);
    expect(screen.getByText('Без перепланирования опоздали бы к 2 клиентам на 35–45 мин и переработка 20 мин')).toBeInTheDocument();
    first.unmount();

    resetStore({ state: makeDelayedState({ late_without_replan: [] }) });
    render(<DiffBanner />);
    const line = screen.getByText('Задержка не привела бы к опозданиям');
    expect(line.tagName).toBe('SPAN');
    expect(line).not.toHaveAttribute('title');
  });

  it('shows no forecast for other events', () => {
    const state = makePlanningState();
    resetStore({ state: { ...state, last_diff: { ...state.last_diff!, delay_forecast: null } } });
    render(<DiffBanner />);
    expect(screen.queryByText(/Без перепланирования|Задержка не привела/)).not.toBeInTheDocument();
  });

  it('names the engineers whose visit order changed', () => {
    render(<DiffBanner />);
    expect(screen.getByText('Изменён порядок: Бригада Арташкин')).toBeInTheDocument();
  });

  it('omits the order line when no route changed its order', () => {
    const state = makePlanningState();
    resetStore({ state: { ...state, last_diff: { ...state.last_diff!, reordered_engineers: [] } } });
    render(<DiffBanner />);
    expect(screen.queryByText(/Изменён порядок/)).not.toBeInTheDocument();
  });

  it('says that a move applied several events and shows the changes of the last one', () => {
    resetStore({ state: makePlanningState(), timelineMove: { applied: 3, back: false } });
    const view = render(<DiffBanner />);
    expect(screen.getByText('Срочная заявка URG-001 в 13:00')).toBeInTheDocument();
    expect(screen.getByText('Применено событий: 3, изменения показаны для последнего')).toBeInTheDocument();
    view.unmount();

    resetStore({ state: makePlanningState(), timelineMove: { applied: 1, back: false } });
    render(<DiffBanner />);
    expect(screen.queryByText(/Применено событий/)).not.toBeInTheDocument();
  });

  it('titles a move back across events with the time of the plan', () => {
    resetStore({ state: makePlanningState({ cursor: '13:40' }), timelineMove: { applied: 0, back: true } });
    render(<DiffBanner />);
    expect(screen.getByText('План на 13:40')).toBeInTheDocument();
    expect(screen.getByText('Изменения показаны для события: Срочная заявка URG-001 в 13:00')).toBeInTheDocument();
  });

  it('stays hidden while the clock plays or is dragged', () => {
    for (const patch of [{ playing: true }, { dragging: true }]) {
      resetStore({ state: makePlanningState(), ...patch });
      const view = render(<DiffBanner />);
      expect(view.container).toBeEmptyDOMElement();
      view.unmount();
    }
  });

  it('remembers every dismissed plan while the clock moves between plans', () => {
    render(<DiffBanner />);
    fireEvent.click(screen.getByRole('button', { name: 'Скрыть' }));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    act(() => useAppStore.setState({ state: makePlanningState({ version: 5 }) }));
    expect(screen.getByRole('status')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Скрыть' }));
    act(() => useAppStore.setState({ state: makePlanningState({ version: 4 }) }));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    act(() => useAppStore.setState({ state: makePlanningState({ version: 5 }) }));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('has no switch to the plan before the event: the clock goes back to it', () => {
    render(<DiffBanner />);
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual(['Скрыть']);
  });

  it('is hidden when there is no diff', () => {
    resetStore({ state: makePlanningState({ last_diff: null }) });
    const { container } = render(<DiffBanner />);
    expect(container).toBeEmptyDOMElement();
  });
});
