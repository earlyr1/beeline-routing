import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { ENGINEER_PALETTE } from '../../lib/colors';
import { percent } from '../../lib/timeline';
import { useAppStore } from '../../store/useAppStore';
import { makeAsapState, makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { BrigadesTab } from './BrigadesTab';
import { ComparisonTab } from './ComparisonTab';
import { PANEL_TABS } from './tabs';
import { TimelineTab } from './TimelineTab';
import { UnassignedTab } from './UnassignedTab';

const brigadeRow = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}`) });

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('panel tabs', () => {
  it('registers the base tabs, the brigades right after the requests and the proposals tab with an unassigned badge', () => {
    expect(PANEL_TABS.map((tab) => tab.id)).toEqual(['requests', 'brigades', 'timeline', 'unassigned', 'comparison', 'proposals']);
    expect(PANEL_TABS[1].title).toBe('Бригады');
    const unassigned = PANEL_TABS.find((tab) => tab.id === 'unassigned');
    expect(unassigned?.badge?.(useAppStore.getState())).toBe(1);
  });

  it('BrigadesTab lists every engineer with transport, shift, visits and mileage of the shown plan', () => {
    render(<BrigadesTab />);
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
    const artashkin = brigadeRow('Бригада Арташкин');
    expect(within(artashkin).getByText('Автомобиль · смена 10:00–22:00 · визитов: 4 · 23,7 км')).toBeInTheDocument();
    expect(within(artashkin).queryByText('Недоступен')).not.toBeInTheDocument();
    expect(artashkin.querySelector('.dot')).toHaveStyle({ background: ENGINEER_PALETTE[0] });
    expect(within(brigadeRow('Бригада Белузин')).getByText('Автомобиль · смена 10:00–22:00 · визитов: 2 · 11,2 км')).toBeInTheDocument();

    const komar = brigadeRow('Бригада Комарь');
    expect(within(komar).getByText('Общественный транспорт и пешком · смена 10:00–22:00 · визитов: 0 · 0,0 км')).toBeInTheDocument();
    expect(within(komar).getByText('Недоступен')).toHaveAttribute('title', 'Недоступен с 13:00');

    act(() => useAppStore.setState({ showPrevious: true }));
    expect(within(brigadeRow('Бригада Арташкин')).getByText('Автомобиль · смена 10:00–22:00 · визитов: 3 · 20,4 км')).toBeInTheDocument();
  });

  it('BrigadesTab opens the brigade page of a clicked engineer and closes an open request card', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104' });
    render(<BrigadesTab />);
    fireEvent.click(brigadeRow('Бригада Белузин'));
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: null, selectedEngineerId: 'E02' });
    expect(brigadeRow('Бригада Белузин')).toHaveClass('brigade-row--selected');
    expect(brigadeRow('Бригада Арташкин')).not.toHaveClass('brigade-row--selected');
  });

  it('UnassignedTab shows the reason and selects the request', () => {
    render(<UnassignedTab />);
    expect(screen.getByText('Не помещается в окно или смену')).toBeInTheDocument();
    expect(screen.getByText(/даже без других заявок Бригада Белузин/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('18754'));
    expect(useAppStore.getState().selectedRequestId).toBe('18754');
  });

  it('UnassignedTab shows the window of a request and «как можно скорее» instead of it', () => {
    const state = makeAsapState();
    const asap = { request_id: 'URG-002', reason_code: 'no_free_engineer_in_window' as const, reason_text: 'Сегодня никто не успевает.' };
    resetStore({ state: { ...state, plan: { ...state.plan, unassigned: [...state.plan.unassigned, asap] } } });
    render(<UnassignedTab />);
    expect(screen.getByText('ул.1-я Новокузьминская, д. 16 к 1 · окно 18:00–20:00 · Работы на подключение и дозаказы')).toBeInTheDocument();
    expect(screen.getByText('ул.Перовская, д. 42 к 1 · как можно скорее с 13:00 · Аварийные работы')).toBeInTheDocument();
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

  it('ComparisonTab says that the dispatchers column ignores applied events', () => {
    render(<ComparisonTab />);
    expect(screen.getByText('Колонка «Диспетчеры» — исходный день, события (3) в ней не учтены.')).toBeInTheDocument();
  });

  it('ComparisonTab has no events note before any event', () => {
    resetStore({ state: makePlanningState({ events: [] }) });
    render(<ComparisonTab />);
    expect(screen.queryByText(/в ней не учтены/)).not.toBeInTheDocument();
  });

  it('TimelineTab renders visit bars and selects a request', () => {
    const { container } = render(<TimelineTab />);
    fireEvent.click(screen.getByRole('button', { name: 'Заявка 50104 14:00–14:45' }));
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
    expect(screen.getByText(/текущее время 13:00/)).toBeInTheDocument();
    // День начинается в 09:00: раньше подписей часов нет.
    expect(container.querySelectorAll('.timeline__tick')[0]).toHaveTextContent('09:00');
    expect(screen.queryByText('08:00')).not.toBeInTheDocument();
  });

  it('TimelineTab draws the now line of every engineer at the clock of the day', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '15:00' });
    const { container } = render(<TimelineTab />);
    expect(screen.getByText(/текущее время 15:00/)).toBeInTheDocument();
    const lines = Array.from(container.querySelectorAll('.timeline__now'));
    expect(lines).toHaveLength(3);
    for (const line of lines) expect(line).toHaveStyle({ left: `${percent({ from: 540, to: 1380 }, 900)}%` });
  });

  it('TimelineTab draws a grey lunch bar that opens nothing and no bar without a lunch', () => {
    const { container } = render(<TimelineTab />);
    expect(screen.getByTitle('Обед 15:55–16:40')).toHaveTextContent('Обед');
    expect(screen.getByTitle('Обед 14:05–14:50')).toHaveClass('timeline__lunch');
    expect(container.querySelectorAll('.timeline__lunch')).toHaveLength(2);
    expect(screen.queryByRole('button', { name: /Обед/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByTitle('Обед 14:05–14:50'));
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: null, selectedEngineerId: null });
  });

  it('TimelineTab opens the brigade page from an engineer label', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104' });
    render(<TimelineTab />);
    fireEvent.click(screen.getByRole('button', { name: 'Бригада Комарь' }));
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: null, selectedEngineerId: 'E03' });
  });
});
