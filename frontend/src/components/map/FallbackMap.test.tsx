import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { StrictMode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, getRouteGeometry: vi.fn(), getReverseGeocode: vi.fn() };
});

import * as L from 'leaflet';
import * as api from '../../api/client';
import { ENGINEER_PALETTE } from '../../lib/colors';
import { overviewBounds, toLatLngBounds } from '../../lib/mapView';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState, makeRouteGeometry } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { FallbackMap } from './FallbackMap';
import { MAP_HEIGHT, MAP_WIDTH, stubMapContainerSize } from './leafletTestEnv';
import { clearRouteGeometryCache } from './useRouteGeometries';

stubMapContainerSize();

const NOTE = 'Подложка OpenStreetMap: ключ Яндекс Карт не задан';

const markerIcons = (container: HTMLElement) => container.querySelectorAll('.leaflet-marker-pane .leaflet-marker-icon');
const routePaths = (container: HTMLElement, color?: string) =>
  Array.from(container.querySelectorAll('.leaflet-overlay-pane path')).filter(
    (path) => color === undefined || path.getAttribute('stroke') === color,
  );

beforeEach(() => {
  vi.restoreAllMocks();
  vi.resetAllMocks();
  clearRouteGeometryCache();
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('FallbackMap', () => {
  it('draws OpenStreetMap tiles with Russian attribution, zoom titles and a note about the base layer', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    const { container } = render(<FallbackMap note={NOTE} detail="Не удалось загрузить Яндекс Карты." />);

    expect(screen.getByText(NOTE)).toHaveAttribute('title', 'Не удалось загрузить Яндекс Карты.');
    expect(container.querySelector('.leaflet-control-attribution')).toHaveTextContent('© участники OpenStreetMap');
    expect(container.querySelector('.leaflet-control-attribution')).not.toHaveTextContent('Leaflet');
    expect(screen.getByTitle('Приблизить')).toBeInTheDocument();
    expect(screen.getByTitle('Отдалить')).toBeInTheDocument();
    const tiles = Array.from(container.querySelectorAll('img.leaflet-tile'));
    expect(tiles.length).toBeGreaterThan(0);
    for (const tile of tiles) expect(tile.getAttribute('src')).toMatch(/^https:\/\/tile\.openstreetmap\.org\/\d+\/\d+\/\d+\.png$/);
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('renders the same markers as the Yandex map with div icons only', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    const { container } = render(<FallbackMap note={NOTE} />);

    expect(markerIcons(container)).toHaveLength(14);
    expect(container.querySelectorAll('img.leaflet-marker-icon')).toHaveLength(0);
    expect(screen.getByTitle('г. Москва, ул Юных Ленинцев, д 83с 4')).toHaveClass('marker', 'marker--office');
    expect(screen.getByTitle('г. Москва, ул Юных Ленинцев, д 83с 4')).toHaveTextContent('Офис');
    expect(screen.getByTitle('Старт: Бригада Арташкин')).toHaveStyle({ borderColor: ENGINEER_PALETTE[0], color: ENGINEER_PALETTE[0] });
    expect(screen.getByTitle('Старт: Бригада Арташкин')).not.toHaveClass('marker--dimmed');
    expect(screen.getByTitle('Старт: Бригада Комарь')).toHaveClass('marker--start', 'marker--dimmed');
    expect(screen.getByTitle(/^URG-001:/)).toHaveClass('marker--urgent', 'marker--changed');
    expect(screen.getByTitle(/^URG-001:/)).toHaveTextContent('2');
    expect(screen.getByTitle(/^URG-001:/)).toHaveStyle({ background: ENGINEER_PALETTE[1] });
    expect(screen.getByTitle(/^10135:/)).toHaveTextContent('×');
    expect(screen.getByTitle(/^18754:/)).toHaveClass('marker--unassigned');
    expect(screen.getByTitle(/^18754:/)).toHaveTextContent('!');
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('draws one route line per leg and replaces straight legs with road geometry', async () => {
    let resolveE02: (value: ReturnType<typeof makeRouteGeometry>) => void = () => undefined;
    vi.mocked(api.getRouteGeometry).mockImplementation((_datasetId, engineerId) => {
      if (engineerId === 'E02') return new Promise((resolve) => (resolveE02 = resolve));
      return Promise.reject(new Error('offline'));
    });
    // Часы до начала смен: никто ещё не выехал, и слой часов не делит отрезки маршрутов.
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '08:00' });
    const { container } = render(<FallbackMap note={NOTE} />);

    expect(routePaths(container)).toHaveLength(6);
    expect(routePaths(container, ENGINEER_PALETTE[0])).toHaveLength(4);
    expect(routePaths(container, ENGINEER_PALETTE[1])).toHaveLength(2);
    for (const path of routePaths(container)) {
      expect(path).toHaveAttribute('stroke-width', '3');
      expect(path).toHaveAttribute('stroke-opacity', '1');
    }
    const straight = routePaths(container, ENGINEER_PALETTE[1])[0].getAttribute('d');

    await act(async () => resolveE02(makeRouteGeometry()));
    await waitFor(() => expect(routePaths(container, ENGINEER_PALETTE[1])[0].getAttribute('d')).not.toBe(straight));
    expect(routePaths(container)).toHaveLength(6);
  });

  it('opens on the whole plan, zooms to a selected request and returns to the whole plan', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    const fitBounds = vi.spyOn(L.Map.prototype, 'fitBounds');
    const setView = vi.spyOn(L.Map.prototype, 'setView');
    render(<FallbackMap note={NOTE} />);
    const overview = toLatLngBounds(overviewBounds(makePlanningState()));
    expect(fitBounds).toHaveBeenCalledTimes(1);
    expect(fitBounds.mock.calls[0][0]).toEqual(overview);

    fireEvent.click(screen.getByTitle(/^50104:/));
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
    expect(setView).toHaveBeenLastCalledWith([55.7212, 37.7336], 14, expect.objectContaining({ animate: true }));
    expect(screen.getByTitle(/^50104:/)).toHaveClass('marker--selected');

    fireEvent.click(screen.getByRole('button', { name: 'Весь план' }));
    expect(useAppStore.getState().selectedRequestId).toBeNull();
    expect(fitBounds).toHaveBeenCalledTimes(2);
    expect(fitBounds.mock.calls[1][0]).toEqual(overview);
    expect(screen.getByTitle(/^50104:/)).not.toHaveClass('marker--selected');
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('selects an engineer from the start marker and dims other engineers and their routes', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '08:00' });
    const { container } = render(<FallbackMap note={NOTE} />);

    fireEvent.click(screen.getByTitle('Старт: Бригада Белузин'));
    expect(useAppStore.getState().selectedEngineerId).toBe('E02');
    expect(screen.getByTitle('Старт: Бригада Арташкин')).toHaveClass('marker--dimmed');
    expect(screen.getByTitle(/^74198:/)).toHaveClass('marker--dimmed');
    expect(screen.getByTitle(/^URG-001:/)).not.toHaveClass('marker--dimmed');
    for (const path of routePaths(container, ENGINEER_PALETTE[1])) expect(path).toHaveAttribute('stroke-width', '6');
    for (const path of routePaths(container, ENGINEER_PALETTE[0])) {
      expect(path).toHaveAttribute('stroke-width', '3');
      expect(Number(path.getAttribute('stroke-opacity'))).toBeCloseTo(0.25, 2);
    }
    expect(markerIcons(container)).toHaveLength(14);

    fireEvent.click(screen.getByTitle('Старт: Бригада Белузин'));
    expect(useAppStore.getState().selectedEngineerId).toBeNull();
    expect(screen.getByTitle('Старт: Бригада Арташкин')).not.toHaveClass('marker--dimmed');
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('passes a clicked map point to the urgent request form in pick mode and ignores markers there', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    const { container } = render(<FallbackMap note={NOTE} />);
    const map = container.querySelector('.leaflet-container') as HTMLElement;

    fireEvent.click(map, { clientX: MAP_WIDTH / 2, clientY: MAP_HEIGHT / 2 });
    expect(useAppStore.getState().pickedPoint).toBeNull();

    act(() => useAppStore.getState().startPick());
    fireEvent.click(screen.getByTitle(/^50104:/));
    expect(useAppStore.getState()).toMatchObject({ pickMode: true, selectedRequestId: null });

    fireEvent.click(map, { clientX: MAP_WIDTH / 2, clientY: MAP_HEIGHT / 2 });
    const [[south, west], [north, east]] = toLatLngBounds(overviewBounds(makePlanningState()));
    const { pickMode, pickedPoint } = useAppStore.getState();
    expect(pickMode).toBe(false);
    expect(pickedPoint?.lon).toBeCloseTo((west + east) / 2, 2);
    expect(pickedPoint?.lat).toBeGreaterThan(south);
    expect(pickedPoint?.lat).toBeLessThan(north);
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('opens the map menu on an empty map click, keeps clicks inside it away from the map and closes it', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    const { container } = render(<FallbackMap note={NOTE} />);
    const map = container.querySelector('.leaflet-container') as HTMLElement;

    fireEvent.click(map, { clientX: MAP_WIDTH / 2, clientY: MAP_HEIGHT / 2 });
    const point = useAppStore.getState().mapMenu;
    expect(point).not.toBeNull();
    const menu = await screen.findByRole('group', { name: 'Меню карты' });
    expect(menu.closest('.leaflet-marker-pane')).not.toBeNull();
    expect(within(menu).getByRole('button', { name: 'Добавить заявку здесь' })).toBeEnabled();

    fireEvent.click(menu, { clientX: 10, clientY: 10 });
    expect(useAppStore.getState().mapMenu).toBe(point);

    fireEvent.click(within(menu).getByRole('button', { name: 'Закрыть меню карты' }));
    expect(useAppStore.getState().mapMenu).toBeNull();
    expect(screen.queryByRole('group', { name: 'Меню карты' })).not.toBeInTheDocument();
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('never opens the map menu from a marker and closes it when a marker is selected', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    const { container } = render(<FallbackMap note={NOTE} />);
    const map = container.querySelector('.leaflet-container') as HTMLElement;

    fireEvent.click(screen.getByTitle('Старт: Бригада Белузин'));
    expect(useAppStore.getState()).toMatchObject({ selectedEngineerId: 'E02', mapMenu: null });

    fireEvent.click(map, { clientX: MAP_WIDTH / 2, clientY: MAP_HEIGHT / 2 });
    expect(await screen.findByRole('group', { name: 'Меню карты' })).toBeInTheDocument();
    fireEvent.click(screen.getByTitle(/^50104:/));
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: '50104', mapMenu: null });
    expect(screen.queryByRole('group', { name: 'Меню карты' })).not.toBeInTheDocument();
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('показывает, где инженеры сейчас, и гасит проеханные отрезки', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    const { container } = render(<FallbackMap note={NOTE} />);

    // Часы фикстуры стоят на 13:00: обе бригады в пути к следующему клиенту.
    expect(screen.getByTitle('Бригада Арташкин: в пути к 50104')).toHaveClass('marker', 'marker--now');
    expect(screen.getByTitle('Бригада Белузин: в пути к URG-001')).toBeInTheDocument();
    // Четыре отрезка базового плана без двух текущих плюс три линии слоя часов.
    expect(routePaths(container)).toHaveLength(7);
    const faded = routePaths(container).filter((path) => Number(path.getAttribute('stroke-opacity')) < 0.9);
    expect(faded).toHaveLength(4);
    for (const path of faded) expect(Number(path.getAttribute('stroke-opacity'))).toBeCloseTo(0.25, 2);
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('ведёт маркер «где сейчас» за часами, не перезапрашивая геометрию', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    render(<FallbackMap note={NOTE} />);
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));

    act(() => useAppStore.getState().setClock('14:10'));
    expect(screen.getByTitle('Бригада Арташкин: работает у 50104')).toBeInTheDocument();
    expect(screen.getByTitle('Бригада Белузин: обед')).toBeInTheDocument();
    expect(api.getRouteGeometry).toHaveBeenCalledTimes(2);
  });

  it('survives the StrictMode double mount and cleans the map up on unmount', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    const { container, unmount } = render(
      <StrictMode>
        <FallbackMap note={NOTE} />
      </StrictMode>,
    );
    expect(container.querySelectorAll('.leaflet-container')).toHaveLength(1);
    expect(markerIcons(container)).toHaveLength(14);
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalled());
    unmount();
    expect(container.querySelector('.leaflet-marker-icon')).toBeNull();
  });
});
