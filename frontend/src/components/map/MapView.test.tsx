import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./yandexLoader', () => ({ loadYandexMaps: vi.fn() }));
vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, getRouteGeometry: vi.fn() };
});

import * as api from '../../api/client';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { stubMapContainerSize } from './leafletTestEnv';
import { MapView } from './MapView';
import { clearRouteGeometryCache } from './useRouteGeometries';
import { loadYandexMaps, type YMapsComponents } from './yandexLoader';

stubMapContainerSize();

const config = (key: string | null) => ({ yandex_maps_api_key: key, llm_enabled: false, osrm_available: true });

const fakeYandex: YMapsComponents = {
  YMap: ({ children }: { children?: ReactNode }) => <div data-testid="yandex-map">{children}</div>,
  YMapDefaultSchemeLayer: () => null,
  YMapDefaultFeaturesLayer: () => null,
  YMapMarker: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  YMapFeature: () => null,
  YMapListener: () => null,
};

beforeEach(() => {
  vi.resetAllMocks();
  clearRouteGeometryCache();
  vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('MapView', () => {
  it('waits for the client config', () => {
    render(<MapView />);
    expect(screen.getByText('Загружаем настройки карты…')).toBeInTheDocument();
  });

  it.each([null, ''])('draws the plan on OpenStreetMap when the Yandex key is %j', async (key) => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), config: config(key) });
    const { container } = render(<MapView />);

    expect(screen.getByText('Подложка OpenStreetMap: ключ Яндекс Карт не задан')).toBeInTheDocument();
    expect(screen.queryByText('Карта отключена')).not.toBeInTheDocument();
    expect(container.querySelectorAll('.leaflet-marker-icon')).toHaveLength(12);
    expect(screen.getByTitle(/^URG-001:/)).toHaveClass('marker--urgent');
    expect(screen.getByRole('button', { name: 'Весь план' })).toBeInTheDocument();
    expect(loadYandexMaps).not.toHaveBeenCalled();
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('switches to OpenStreetMap when Yandex Maps fail to load', async () => {
    const reason = 'Не удалось загрузить Яндекс Карты. Проверьте интернет и ключ API.';
    vi.mocked(loadYandexMaps).mockRejectedValue(new Error(reason));
    resetStore({ datasetId: 'd_test', state: makePlanningState(), config: config('test-key') });
    const { container } = render(<MapView />);

    expect(screen.getByText('Загружаем Яндекс Карты…')).toBeInTheDocument();
    expect(await screen.findByText('Яндекс Карты недоступны, показана подложка OpenStreetMap')).toHaveAttribute('title', reason);
    expect(screen.queryByText('Карта не загрузилась')).not.toBeInTheDocument();
    // Заметка появляется в том же коммите, а маркеры Leaflet добавляет эффект чуть позже.
    await waitFor(() => expect(container.querySelectorAll('.leaflet-marker-icon')).toHaveLength(12));
    expect(loadYandexMaps).toHaveBeenCalledWith('test-key');
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('uses Yandex Maps when they load', async () => {
    vi.mocked(loadYandexMaps).mockResolvedValue(fakeYandex);
    resetStore({ datasetId: 'd_test', state: makePlanningState(), config: config('test-key') });
    const { container } = render(<MapView />);

    expect(await screen.findByTestId('yandex-map')).toBeInTheDocument();
    expect(screen.getByTitle(/^URG-001:/)).toHaveClass('marker--urgent');
    expect(container.querySelector('.leaflet-container')).toBeNull();
    expect(screen.queryByText(/OpenStreetMap/)).not.toBeInTheDocument();
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });
});
