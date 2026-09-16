import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { unavailableEvent } from '../lib/events';
import { percent } from '../lib/timeline';
import { useAppStore } from '../store/useAppStore';
import { makeAsapState, makeDelayEvent, makePlanningState, makeTimelineItem, makeTransportChangeEvent } from '../test/fixtures';
import { resetStore } from '../test/store';
import { RouteCard } from './RouteCard';

const card = () => screen.getByRole('region', { name: 'Бригада' });
const bodyRows = () => within(card()).getAllByRole('row').slice(1);
const cells = (row: HTMLElement) => within(row).getAllByRole('cell').map((cell) => cell.textContent);
const listItems = (name: string) =>
  within(within(card()).getByRole('list', { name })).getAllByRole('listitem').map((item) => item.textContent);
const action = (name: string) => within(card()).getByRole('button', { name });
const timeline = () => within(card()).getByRole('group', { name: 'Таймлайн бригады' });

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedEngineerId: 'E01' });
});

describe('RouteCard', () => {
  it('describes the engineer, the visits in route order and the totals', () => {
    render(<RouteCard />);
    expect(within(card()).getByRole('heading', { name: 'Бригада Арташкин' })).toBeInTheDocument();
    expect(
      within(card()).getByText('Локальные работы, Работы на подключение и дозаказы · Автомобиль · смена 10:00–22:00'),
    ).toBeInTheDocument();
    expect(
      within(card()).getByText(
        'Визитов: 4 · оборудование 2 единицы · пробег 23,7 км · в пути 2 ч 15 мин · окончание работ 15:55, конец смены 22:00',
      ),
    ).toBeInTheDocument();

    const rows = bodyRows();
    expect(rows).toHaveLength(5);
    expect(cells(rows[0])).toEqual(['1', '74198Закреплена', '10:00–12:00', '09:35', '10:00', '120 мин', '6,1 км']);
    expect(cells(rows[3])).toEqual(['4', '46393', '15:00–17:00', '15:10', '15:10', '110 мин', '3,9 км']);
    expect(cells(rows[4])).toEqual(['', 'Обед', '15:55–16:40']);
  });

  it('lists the lunch among the visits in time order and the lunch row opens nothing', () => {
    useAppStore.setState({ showPrevious: true });
    render(<RouteCard />);
    const rows = bodyRows();
    expect(rows.map((row) => cells(row).slice(0, 3))).toEqual([
      ['1', '74198Закреплена', '10:00–12:00'],
      ['2', '86160Закреплена', '12:00–14:00'],
      ['', 'Обед', '13:30–14:15'],
      ['3', '46393', '15:00–17:00'],
    ]);
    fireEvent.click(rows[2]);
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: null, selectedEngineerId: 'E01' });
  });

  it('shows no lunch for a route without a lunch', () => {
    const state = makePlanningState();
    const plan = { ...state.plan, routes: state.plan.routes.map((route) => ({ ...route, lunch: null })) };
    resetStore({ datasetId: 'd_test', state: { ...state, plan }, selectedEngineerId: 'E01' });
    render(<RouteCard />);
    expect(bodyRows()).toHaveLength(4);
    expect(within(card()).queryByText('Обед')).not.toBeInTheDocument();
    expect(timeline().querySelector('.timeline__lunch')).toBeNull();
  });

  it('считает оборудование по визитам показанного плана и молчит, когда его везти не надо', () => {
    useAppStore.setState({ selectedEngineerId: 'E02' });
    const view = render(<RouteCard />);
    expect(within(card()).getByText(/^Визитов: 2 · пробег/)).toBeInTheDocument();
    expect(within(card()).queryByText(/оборудование/)).not.toBeInTheDocument();
    view.unmount();

    // Одна заявка с оборудованием у Белузина: единица в единственном числе.
    const state = makePlanningState();
    const requests = state.requests.map((request) => (request.id === '84627' ? { ...request, needs_equipment: true } : request));
    resetStore({ datasetId: 'd_test', state: { ...state, requests }, selectedEngineerId: 'E02' });
    const single = render(<RouteCard />);
    expect(within(card()).getByText(/^Визитов: 2 · оборудование 1 единица · пробег/)).toBeInTheDocument();
    single.unmount();

    // В плане до события у Арташкина нет визита 50104, но обе заявки с оборудованием остаются.
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedEngineerId: 'E01', showPrevious: true });
    render(<RouteCard />);
    expect(within(card()).getByText(/^Визитов: 3 · оборудование 2 единицы · пробег/)).toBeInTheDocument();
  });

  it('explains the route in dispatcher language', () => {
    render(<RouteCard />);
    expect(listItems('Почему такой маршрут')).toEqual([
      'Маршрут 23,7 км — 68% пробега всего плана; порядок визитов следует окнам заявок.',
      'Все визиты начинаются внутри окон, минимальный запас до конца окна 1 ч 50 мин (заявка 46393).',
      'Работы заканчиваются в 15:55, до конца смены в 22:00 остаётся 6 ч 5 мин.',
      'Визиты 74198, 86160 начаты до события и закреплены: перепланирование их не меняет.',
    ]);
  });

  it('opens a request from the route', () => {
    render(<RouteCard />);
    fireEvent.click(bodyRows()[2]);
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
    expect(useAppStore.getState().selectedEngineerId).toBe('E01');
  });

  it('closes the brigade page and clears the selected engineer', () => {
    render(<RouteCard />);
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть бригаду' }));
    expect(useAppStore.getState().selectedEngineerId).toBeNull();
    expect(screen.queryByRole('region', { name: 'Бригада' })).not.toBeInTheDocument();
  });

  it('shows the route of the plan before the event when it is displayed', () => {
    useAppStore.setState({ showPrevious: true });
    render(<RouteCard />);
    expect(within(card()).getByText('Маршрут по плану до события.')).toBeInTheDocument();
    expect(bodyRows().map((row) => cells(row)[1])).toEqual(['74198Закреплена', '86160Закреплена', 'Обед', '46393']);
    expect(within(timeline()).getByRole('button', { name: 'Заявка 46393 15:00–15:45' })).toBeInTheDocument();
  });

  it('explains an unavailable engineer without visits', () => {
    useAppStore.setState({ selectedEngineerId: 'E03' });
    render(<RouteCard />);
    expect(within(card()).getByText('Локальные работы · Общественный транспорт и пешком · смена 10:00–22:00 · недоступен с 13:00')).toBeInTheDocument();
    expect(listItems('Почему такой маршрут')).toEqual(['В этом плане у инженера нет визитов.', 'Инженер недоступен с 13:00.']);
    expect(within(card()).queryByRole('table')).not.toBeInTheDocument();
  });

  it('opens the transport change, the delay and the unavailability dialogs for this engineer', () => {
    render(<RouteCard />);
    expect(
      within(card().querySelector('.explanation__actions') as HTMLElement)
        .getAllByRole('button')
        .map((button) => button.textContent),
    ).toEqual(['Смена транспорта', 'Задержка', 'Недоступен', '✕']);

    fireEvent.click(action('Смена транспорта'));
    expect(useAppStore.getState().engineerDialog).toEqual({ kind: 'transport', engineerId: 'E01' });

    fireEvent.click(action('Задержка'));
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: true, delayEngineerId: 'E01', engineerDialog: null });

    fireEvent.click(action('Недоступен'));
    expect(useAppStore.getState()).toMatchObject({ engineerDialog: { kind: 'unavailable', engineerId: 'E01' }, delayDialogOpen: false });
  });

  it('disables every action while replanning and in the plan before the event', () => {
    for (const patch of [{ busy: true }, { showPrevious: true }]) {
      resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedEngineerId: 'E01', ...patch });
      const view = render(<RouteCard />);
      for (const name of ['Смена транспорта', 'Задержка', 'Недоступен']) expect(action(name)).toBeDisabled();
      expect(action('Задержка')).not.toHaveAttribute('title');
      view.unmount();
    }
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: false, engineerDialog: null });
  });

  it('explains why an unavailable engineer gets no delay, unavailability or transport change', () => {
    useAppStore.setState({ selectedEngineerId: 'E03' });
    render(<RouteCard />);
    expect(action('Задержка')).toBeDisabled();
    expect(action('Задержка')).toHaveAttribute('title', 'Инженер недоступен, задержку поставить нельзя');
    expect(action('Недоступен')).toBeDisabled();
    expect(action('Недоступен')).toHaveAttribute('title', 'Инженер уже недоступен с 13:00');
    expect(action('Смена транспорта')).toBeDisabled();
    expect(action('Смена транспорта')).toHaveAttribute('title', 'Инженер недоступен, сменить транспорт нельзя');
  });

  it('draws a personal timeline whose bars open the requests of this engineer', () => {
    const { container } = render(<RouteCard />);
    const personal = timeline();
    expect(within(personal).getByText('09:00')).toBeInTheDocument();
    expect(within(personal).getAllByRole('button').map((bar) => bar.getAttribute('aria-label'))).toEqual([
      'Заявка 74198 10:00–11:00',
      'Заявка 86160 12:00–13:00',
      'Заявка 50104 14:00–14:45',
      'Заявка 46393 15:10–15:55',
    ]);
    expect(personal.querySelectorAll('.timeline__window')).toHaveLength(4);
    expect(personal.querySelectorAll('.timeline__shift')).toHaveLength(1);
    expect(personal.querySelector('.timeline__now')).not.toBeNull();
    expect(container.querySelector('.timeline__unavailable')).toBeNull();

    const lunch = within(personal).getByTitle('Обед 15:55–16:40');
    expect(lunch).toHaveClass('timeline__lunch');
    expect(lunch).toHaveTextContent('Обед');
    expect(lunch.tagName).toBe('DIV');
    fireEvent.click(lunch);
    expect(useAppStore.getState().selectedRequestId).toBeNull();

    fireEvent.click(within(personal).getByRole('button', { name: 'Заявка 50104 14:00–14:45' }));
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: '50104', selectedEngineerId: 'E01' });
  });

  it('shows «как можно скорее» instead of the window in the visits and in the titles of the timeline bars', () => {
    resetStore({ datasetId: 'd_test', state: makeAsapState(), selectedEngineerId: 'E02' });
    render(<RouteCard />);
    const rows = bodyRows();
    expect(cells(rows[0]).slice(0, 3)).toEqual(['1', '84627Закреплена', '12:00–14:00']);
    expect(cells(rows[2]).slice(0, 3)).toEqual(['3', 'URG-002', 'как можно скорее с 13:00']);
    expect(within(timeline()).getByRole('button', { name: 'Заявка URG-002 14:25–15:25' })).toHaveAttribute(
      'title',
      'URG-002: 14:25–15:25, как можно скорее с 13:00',
    );
    expect(within(timeline()).getByRole('button', { name: 'Заявка 84627 12:00–12:40' })).toHaveAttribute(
      'title',
      '84627: 12:00–12:40, окно 12:00–14:00',
    );
  });

  it('hatches the personal timeline of an unavailable engineer', () => {
    useAppStore.setState({ selectedEngineerId: 'E03' });
    render(<RouteCard />);
    expect(within(timeline()).getByTitle('Инженер недоступен')).toHaveClass('timeline__unavailable');
    expect(within(timeline()).queryAllByRole('button')).toHaveLength(0);
  });

  it('lists the timeline events of this brigade newest first: applied, ahead and rejected', () => {
    const state = makePlanningState();
    const timeline = [
      makeTimelineItem({ id: 'tl_1', event: makeTransportChangeEvent() }),
      makeTimelineItem({ id: 'tl_2', event: makeDelayEvent({ time: '14:00', engineer_id: 'E02' }), status: 'pending' }),
      makeTimelineItem({ id: 'tl_3', event: makeDelayEvent({ time: '14:30' }), status: 'pending' }),
      makeTimelineItem({ id: 'tl_4', event: unavailableEvent('E01', '16:00'), status: 'rejected', reason: 'Инженер уже недоступен.' }),
    ];
    resetStore({ datasetId: 'd_test', state: { ...state, timeline }, selectedEngineerId: 'E01' });
    render(<RouteCard />);
    expect(within(card()).getByRole('heading', { name: 'События бригады' })).toBeInTheDocument();
    expect(listItems('События бригады')).toEqual([
      '16:00 Инженер недоступен: Бригада Арташкин с 16:00 · отклонено: Инженер уже недоступен.',
      '14:30 Задержка: Бригада Арташкин на 150 мин с 14:30 · впереди',
      '13:30 Смена транспорта: Бригада Арташкин, Автомобиль → Велосипед с 13:30 · применено',
    ]);
    expect(within(card()).getByText('· отклонено: Инженер уже недоступен.')).toHaveClass('brigade-events__status', 'brigade-events__status--rejected');
    expect(within(card()).getByText('· впереди')).toHaveClass('brigade-events__status--pending');
  });

  it('draws the now line of the personal timeline at the clock of the day', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedEngineerId: 'E01', clock: '15:00' });
    render(<RouteCard />);
    expect(timeline().querySelector('.timeline__now')).toHaveStyle({ left: `${percent({ from: 540, to: 1380 }, 900)}%` });
  });

  it('hides the events section of a brigade without events', () => {
    render(<RouteCard />);
    expect(within(card()).queryByRole('heading', { name: 'События бригады' })).not.toBeInTheDocument();
    expect(within(card()).queryByRole('list', { name: 'События бригады' })).not.toBeInTheDocument();
  });

  it('renders nothing without a selected engineer or while a request card is open', () => {
    useAppStore.setState({ selectedEngineerId: null });
    const first = render(<RouteCard />);
    expect(first.container).toBeEmptyDOMElement();
    first.unmount();
    useAppStore.setState({ selectedEngineerId: 'E01', selectedRequestId: '50104' });
    expect(render(<RouteCard />).container).toBeEmptyDOMElement();
  });
});
