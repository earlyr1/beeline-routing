import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./yandexLoader', () => ({ loadYandexMaps: vi.fn() }));

import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { MapView } from './MapView';
import { loadYandexMaps } from './yandexLoader';

beforeEach(() => {
  vi.resetAllMocks();
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('MapView', () => {
  it('waits for the client config', () => {
    render(<MapView />);
    expect(screen.getByText('Загружаем настройки карты…')).toBeInTheDocument();
  });

  it('explains how to enable the map when there is no API key', () => {
    resetStore({ state: makePlanningState(), config: { yandex_maps_api_key: null, llm_enabled: false, osrm_available: true } });
    render(<MapView />);
    expect(screen.getByText('Карта отключена')).toBeInTheDocument();
    expect(loadYandexMaps).not.toHaveBeenCalled();
  });

  it('shows the loader error when Yandex Maps fail to load', async () => {
    vi.mocked(loadYandexMaps).mockRejectedValue(new Error('Не удалось загрузить Яндекс Карты. Проверьте интернет и ключ API.'));
    resetStore({ state: makePlanningState(), config: { yandex_maps_api_key: 'test-key', llm_enabled: false, osrm_available: true } });
    render(<MapView />);
    expect(await screen.findByText('Карта не загрузилась')).toBeInTheDocument();
    expect(loadYandexMaps).toHaveBeenCalledWith('test-key');
  });
});
