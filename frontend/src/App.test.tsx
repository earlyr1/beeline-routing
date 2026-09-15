import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api/client')>();
  return { ...actual, getConfig: vi.fn(), getExplanation: vi.fn(), getRouteGeometry: vi.fn() };
});

import * as api from './api/client';
import { App } from './App';
import { makePlanningState } from './test/fixtures';
import { resetStore } from './test/store';

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getConfig).mockResolvedValue({ yandex_maps_api_key: null, llm_enabled: false, osrm_available: true });
  resetStore();
});

describe('App', () => {
  it('starts on the upload screen', async () => {
    render(<App />);
    expect(screen.getByRole('heading', { name: 'Планирование маршрутов выездных инженеров' })).toBeInTheDocument();
    await vi.waitFor(() => expect(api.getConfig).toHaveBeenCalled());
  });

  it('shows the main screen with metrics, event actions, tabs and the map area once a plan exists', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    render(<App />);
    expect(screen.getByText('Восток')).toBeInTheDocument();
    expect(screen.getByText('Сейчас 13:00')).toBeInTheDocument();
    expect(screen.getByText('2 из 3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Срочная заявка' })).toBeInTheDocument();
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Заявки', 'Таймлайн', 'Неназначенные1', 'Сравнение', 'Рекомендуемые изменения']);
    expect(await screen.findByText('Карта отключена')).toBeInTheDocument();
  });
});
