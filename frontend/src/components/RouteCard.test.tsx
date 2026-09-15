import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../store/useAppStore';
import { makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { RouteCard } from './RouteCard';

const card = () => screen.getByRole('region', { name: 'Маршрут инженера' });
const bodyRows = () => within(card()).getAllByRole('row').slice(1);
const cells = (row: HTMLElement) => within(row).getAllByRole('cell').map((cell) => cell.textContent);

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
      within(card()).getByText('Визитов: 4 · пробег 23,7 км · в пути 2 ч 15 мин · окончание работ 15:55, конец смены 22:00'),
    ).toBeInTheDocument();

    const rows = bodyRows();
    expect(rows).toHaveLength(4);
    expect(cells(rows[0])).toEqual(['1', '74198Закреплена', '10:00–12:00', '09:35', '10:00', '120 мин', '6,1 км']);
    expect(cells(rows[3])).toEqual(['4', '46393', '15:00–17:00', '15:10', '15:10', '110 мин', '3,9 км']);
  });

  it('explains the route in dispatcher language', () => {
    render(<RouteCard />);
    expect(within(card()).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
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

  it('closes the card and clears the selected engineer', () => {
    render(<RouteCard />);
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть маршрут' }));
    expect(useAppStore.getState().selectedEngineerId).toBeNull();
    expect(screen.queryByRole('region', { name: 'Маршрут инженера' })).not.toBeInTheDocument();
  });

  it('shows the route of the plan before the event when it is displayed', () => {
    useAppStore.setState({ showPrevious: true });
    render(<RouteCard />);
    expect(within(card()).getByText('Маршрут по плану до события.')).toBeInTheDocument();
    expect(bodyRows().map((row) => cells(row)[1])).toEqual(['74198Закреплена', '86160Закреплена', '46393']);
  });

  it('explains an unavailable engineer without visits', () => {
    useAppStore.setState({ selectedEngineerId: 'E03' });
    render(<RouteCard />);
    expect(within(card()).getByText('Локальные работы · Пешеход · смена 10:00–22:00 · недоступен с 13:00')).toBeInTheDocument();
    expect(within(card()).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'В этом плане у инженера нет визитов.',
      'Инженер недоступен с 13:00.',
    ]);
    expect(within(card()).queryByRole('table')).not.toBeInTheDocument();
  });

  it('opens the delay dialog for the engineer of the route', () => {
    render(<RouteCard />);
    const button = within(card()).getByRole('button', { name: 'Задержка' });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: true, delayEngineerId: 'E01' });
  });

  it('disables the delay while replanning, before the event and for an unavailable engineer', () => {
    const delayButton = () => within(card()).getByRole('button', { name: 'Задержка' });
    const cases = [{ busy: true }, { showPrevious: true }, { selectedEngineerId: 'E03' }];
    for (const patch of cases) {
      resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedEngineerId: 'E01', ...patch });
      const view = render(<RouteCard />);
      expect(delayButton()).toBeDisabled();
      view.unmount();
    }
    expect(useAppStore.getState().delayDialogOpen).toBe(false);
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
