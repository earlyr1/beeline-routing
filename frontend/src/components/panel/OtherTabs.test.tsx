import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { ENGINEER_PALETTE } from '../../lib/colors';
import { percent } from '../../lib/timeline';
import { useAppStore } from '../../store/useAppStore';
import { makeDataUrgentState, makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { BrigadesTab } from './BrigadesTab';
import { ComparisonTab } from './ComparisonTab';
import { PANEL_TABS } from './tabs';
import { TimelineTab } from './TimelineTab';

const brigadeRow = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}`) });

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('panel tabs', () => {
  it('registers the base tabs, the brigades right after the requests and the unassigned count on the requests tab', () => {
    expect(PANEL_TABS.map((tab) => tab.id)).toEqual(['requests', 'brigades', 'timeline', 'comparison', 'proposals', 'communications']);
    expect(PANEL_TABS[1].title).toBe('Бригады');
    // Отдельной вкладки неназначенных нет: их число стоит на «Заявках», там же фильтр «Без исполнителя».
    const requests = PANEL_TABS[0];
    expect(requests.badge?.(useAppStore.getState())).toBe(1);
    expect(requests.badgeTitle).toBe('Заявки без исполнителя');
    const state = makePlanningState();
    resetStore({ state: { ...state, plan: { ...state.plan, unassigned: [] } } });
    expect(requests.badge?.(useAppStore.getState())).toBeNull();
  });

  it('BrigadesTab lists every engineer with transport, shift, visits and mileage of the current plan', () => {
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
  });

  it('BrigadesTab opens the brigade page of a clicked engineer and closes an open request card', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104' });
    render(<BrigadesTab />);
    fireEvent.click(brigadeRow('Бригада Белузин'));
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: null, selectedEngineerId: 'E02' });
    expect(brigadeRow('Бригада Белузин')).toHaveClass('brigade-row--selected');
    expect(brigadeRow('Бригада Арташкин')).not.toHaveClass('brigade-row--selected');
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

  it('TimelineTab marks a selected urgent bar as both urgent and selected', () => {
    resetStore({ datasetId: 'd_test', state: makeDataUrgentState(), selectedRequestId: '50104' });
    render(<TimelineTab />);
    // Рамки срочности и выделения рисует одно свойство box-shadow: в стилях у них общее правило, здесь важны оба класса.
    const bar = screen.getByRole('button', { name: 'Заявка URG-50104 14:00–14:45' });
    expect(bar).toHaveClass('timeline__bar--urgent', 'timeline__bar--selected');
    expect(bar).toHaveAttribute('title', 'URG-50104: 14:00–14:45, окно 14:00–16:00');
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
