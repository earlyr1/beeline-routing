import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api/client')>();
  return { ...actual, getConfig: vi.fn(), getPlanningState: vi.fn(), getExplanation: vi.fn(), getRouteGeometry: vi.fn() };
});

import * as api from './api/client';
import { App } from './App';
import { SESSION_DATASET_KEY } from './store/useAppStore';
import { makePlanningState } from './test/fixtures';
import { resetStore } from './test/store';

beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
  vi.mocked(api.getConfig).mockResolvedValue({ yandex_maps_api_key: null, llm_enabled: false, osrm_available: true });
  resetStore();
});

describe('App', () => {
  it('starts on the upload screen', async () => {
    render(<App />);
    expect(screen.getByRole('heading', { name: 'Планирование маршрутов выездных инженеров' })).toBeInTheDocument();
    await vi.waitFor(() => expect(api.getConfig).toHaveBeenCalled());
    expect(api.getPlanningState).not.toHaveBeenCalled();
  });

  it('shows the main screen with metrics, event actions, tabs and the map area once a plan exists', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    render(<App />);
    expect(screen.getByText('Восток')).toBeInTheDocument();
    expect(screen.getByText('Сейчас 13:00')).toBeInTheDocument();
    expect(screen.getByText('2 из 3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Срочная заявка' })).toBeInTheDocument();
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Заявки', 'Таймлайн', 'Неназначенные1', 'Сравнение', 'Рекомендуемые изменения']);
    expect(await screen.findByText('Подложка OpenStreetMap: ключ Яндекс Карт не задан')).toBeInTheDocument();
  });

  it('reopens the saved plan after a page reload', async () => {
    sessionStorage.setItem(SESSION_DATASET_KEY, 'd_test');
    vi.mocked(api.getPlanningState).mockResolvedValue(makePlanningState());
    render(<App />);
    expect(await screen.findByText('Сейчас 13:00')).toBeInTheDocument();
    expect(api.getPlanningState).toHaveBeenCalledWith('d_test');
  });

  it('stays on the upload screen when the saved dataset is gone', async () => {
    sessionStorage.setItem(SESSION_DATASET_KEY, 'd_gone');
    vi.mocked(api.getPlanningState).mockRejectedValue(new api.ApiError(404, 'Набор данных не найден'));
    render(<App />);
    await vi.waitFor(() => expect(sessionStorage.getItem(SESSION_DATASET_KEY)).toBeNull());
    expect(screen.getByRole('heading', { name: 'Планирование маршрутов выездных инженеров' })).toBeInTheDocument();
  });
});
