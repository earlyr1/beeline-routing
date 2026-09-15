import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, getRouteGeometry: vi.fn() };
});

import * as api from '../../api/client';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState, makeRouteGeometry } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { MapContent } from './MapContent';
import { clearRouteGeometryCache } from './useRouteGeometries';
import type { LngLat, LineStyle, MapLocation, YMapsComponents } from './yandexLoader';

// Фейковые компоненты вместо Яндекс Карт: те же пропсы, обычный DOM.
const fake: YMapsComponents = {
  YMap: ({ location, children }: { location: MapLocation; children?: ReactNode }) => (
    <div data-testid="map" data-center={location.center.join(',')}>
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
    <button type="button" onClick={() => onClick?.(undefined, { coordinates: [37.8, 55.71] })}>
      map-click
    </button>
  ),
};

const firstPoints = () =>
  screen.getAllByTestId('feature').map((feature) => (JSON.parse(feature.getAttribute('data-coordinates') as string) as LngLat[])[0]);

beforeEach(() => {
  vi.resetAllMocks();
  clearRouteGeometryCache();
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('MapContent', () => {
  it('renders engineer start markers, a neutral office marker and request markers', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
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
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
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
});
