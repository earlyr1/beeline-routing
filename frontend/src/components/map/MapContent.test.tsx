import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, getRouteGeometry: vi.fn(), getReverseGeocode: vi.fn() };
});

import * as api from '../../api/client';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState, makeRouteGeometry } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { MapContent } from './MapContent';
import { clearRouteGeometryCache } from './useRouteGeometries';
import type { LngLat, LineStyle, MapLocation, YMapsComponents } from './yandexLoader';

const CLICKED: LngLat = [37.8, 55.71];

// Фейковые компоненты вместо Яндекс Карт: те же пропсы, обычный DOM.
// Слушатель кликов умеет кликнуть по пустому месту и по объекту карты: тогда первым аргументом приходит объект.
const fake: YMapsComponents = {
  YMap: ({ location, children }: { location: MapLocation; children?: ReactNode }) => (
    <div
      data-testid="map"
      data-center={'center' in location ? location.center.join(',') : ''}
      data-bounds={'bounds' in location ? JSON.stringify(location.bounds) : ''}
    >
      {children}
    </div>
  ),
  YMapDefaultSchemeLayer: () => null,
  YMapDefaultFeaturesLayer: () => null,
  YMapMarker: ({ coordinates, children }: { coordinates: LngLat; children?: ReactNode }) => (
    <div data-testid="marker" data-coordinates={coordinates.join(',')}>
      {children}
    </div>
  ),
  YMapFeature: ({ geometry, style }: { geometry: { coordinates: LngLat[] }; style?: LineStyle }) => (
    <div data-testid="feature" data-coordinates={JSON.stringify(geometry.coordinates)} data-color={style?.stroke[0].color} />
  ),
  YMapListener: ({ onClick }: { onClick?: (object: unknown, event: { coordinates: LngLat }) => void }) => (
    <>
      <button type="button" onClick={() => onClick?.(undefined, { coordinates: CLICKED })}>
        map-click
      </button>
      <button type="button" onClick={() => onClick?.({ type: 'marker', entity: {} }, { coordinates: CLICKED })}>
        object-click
      </button>
    </>
  ),
};

const firstPoints = () =>
  screen.getAllByTestId('feature').map((feature) => (JSON.parse(feature.getAttribute('data-coordinates') as string) as LngLat[])[0]);
const menu = () => screen.getByRole('group', { name: 'Меню карты' });
const queryMenu = () => screen.queryByRole('group', { name: 'Меню карты' });

beforeEach(() => {
  vi.resetAllMocks();
  clearRouteGeometryCache();
  vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('MapContent', () => {
  it('renders engineer start markers, a neutral office marker and request markers', async () => {
    render(<MapContent components={fake} />);
    expect(screen.getByTitle('Старт: Бригада Арташкин').parentElement).toHaveAttribute('data-coordinates', '37.781,55.7005');
    expect(screen.getByTitle('Старт: Бригада Комарь')).toHaveClass('marker--dimmed');
    expect(screen.getByTitle('г. Москва, ул Юных Ленинцев, д 83с 4')).toHaveClass('marker--office');
    expect(screen.getByTitle(/^URG-001:/)).toHaveClass('marker--urgent', 'marker--changed');
    expect(screen.getByTitle(/^10135:/)).toHaveTextContent('×');
    expect(screen.getByTitle(/^18754:/)).toHaveClass('marker--unassigned');
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('draws legs from each engineer start and replaces them with backend geometry', async () => {
    vi.mocked(api.getRouteGeometry).mockImplementation(async (_datasetId, engineerId) => {
      if (engineerId === 'E02') return makeRouteGeometry();
      throw new Error('offline');
    });
    render(<MapContent components={fake} />);
    expect(firstPoints()).toContainEqual([37.781, 55.7005]);
    expect(firstPoints()).not.toContainEqual([37.7862, 55.7075]);
    const road = JSON.stringify(makeRouteGeometry().legs[0].coordinates);
    await waitFor(() =>
      expect(screen.getAllByTestId('feature').some((feature) => feature.getAttribute('data-coordinates') === road)).toBe(true),
    );
  });

  it('selects requests and engineers from markers and picks a point in pick mode', async () => {
    render(<MapContent components={fake} />);
    fireEvent.click(screen.getByTitle(/^50104:/));
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
    expect(screen.getByTestId('map')).toHaveAttribute('data-center', '37.7336,55.7212');

    fireEvent.click(screen.getByTitle('Старт: Бригада Белузин'));
    expect(useAppStore.getState().selectedEngineerId).toBe('E02');

    act(() => useAppStore.getState().startPick());
    fireEvent.click(screen.getByRole('button', { name: 'map-click' }));
    expect(useAppStore.getState()).toMatchObject({ pickMode: false, pickedPoint: { lat: 55.71, lon: 37.8 } });
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalled());
  });

  it('picks a point on a click on a map object in pick mode without opening the menu', async () => {
    render(<MapContent components={fake} />);
    act(() => useAppStore.getState().startPick('urgent'));
    fireEvent.click(screen.getByRole('button', { name: 'object-click' }));
    expect(useAppStore.getState()).toMatchObject({ pickMode: false, pickedPoint: { lat: 55.71, lon: 37.8 }, mapMenu: null });
    expect(queryMenu()).not.toBeInTheDocument();
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalled());
  });

  it('opens the map menu at an empty place but never on a click on a marker', async () => {
    render(<MapContent components={fake} />);
    fireEvent.click(screen.getByRole('button', { name: 'object-click' }));
    expect(useAppStore.getState().mapMenu).toBeNull();
    expect(queryMenu()).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'map-click' }));
    expect(useAppStore.getState().mapMenu).toEqual({ lat: 55.71, lon: 37.8 });
    expect(menu().parentElement).toHaveAttribute('data-coordinates', '37.8,55.71');
    expect(within(menu()).getByRole('button', { name: 'Добавить заявку здесь' })).toBeEnabled();

    fireEvent.click(within(menu()).getByRole('button', { name: 'Закрыть меню карты' }));
    expect(useAppStore.getState().mapMenu).toBeNull();
    expect(queryMenu()).not.toBeInTheDocument();
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalled());
  });

  it('closes the map menu with Escape and when a marker is selected', async () => {
    render(<MapContent components={fake} />);
    fireEvent.click(screen.getByRole('button', { name: 'map-click' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(useAppStore.getState().mapMenu).toBeNull();
    expect(queryMenu()).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'map-click' }));
    fireEvent.click(screen.getByTitle(/^50104:/));
    expect(useAppStore.getState()).toMatchObject({ mapMenu: null, selectedRequestId: '50104' });
    expect(queryMenu()).not.toBeInTheDocument();
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalled());
  });

  it('adds an urgent request at the point of the menu and looks up its address', async () => {
    vi.mocked(api.getReverseGeocode).mockResolvedValue({ address: 'Москва, Ташкентская улица, 16к2', precision: 'house' });
    render(<MapContent components={fake} />);
    fireEvent.click(screen.getByRole('button', { name: 'map-click' }));
    fireEvent.click(within(menu()).getByRole('button', { name: 'Добавить заявку здесь' }));
    expect(useAppStore.getState()).toMatchObject({
      mapMenu: null,
      toolbarDialog: 'urgent',
      pickFor: 'urgent',
      pickedPoint: { lat: 55.71, lon: 37.8 },
    });
    expect(queryMenu()).not.toBeInTheDocument();
    expect(api.getReverseGeocode).toHaveBeenCalledWith(55.71, 37.8);
    await waitFor(() => expect(useAppStore.getState().urgentSuggestedAddress).toBe('Москва, Ташкентская улица, 16к2'));
  });

  it('does not offer adding a request in the plan before the event or while replanning', async () => {
    render(<MapContent components={fake} />);
    fireEvent.click(screen.getByRole('button', { name: 'map-click' }));
    act(() => useAppStore.setState({ showPrevious: true }));
    expect(within(menu()).getByRole('button', { name: 'Добавить заявку здесь' })).toBeDisabled();
    act(() => useAppStore.setState({ showPrevious: false, busy: true }));
    expect(within(menu()).getByRole('button', { name: 'Добавить заявку здесь' })).toBeDisabled();
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalled());
  });

  it('opens on an overview of the whole plan and returns to it after a request is closed', async () => {
    render(<MapContent components={fake} />);
    const map = screen.getByTestId('map');
    expect(map.getAttribute('data-bounds')).not.toBe('');
    expect(map).toHaveAttribute('data-center', '');

    fireEvent.click(screen.getByTitle(/^50104:/));
    expect(map).toHaveAttribute('data-center', '37.7336,55.7212');

    fireEvent.click(screen.getByRole('button', { name: 'Весь план' }));
    expect(useAppStore.getState().selectedRequestId).toBeNull();
    expect(map.getAttribute('data-bounds')).not.toBe('');
    expect(map).toHaveAttribute('data-center', '');
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalled());
  });
});
