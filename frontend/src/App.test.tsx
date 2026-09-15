import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api/client')>();
  return { ...actual, getConfig: vi.fn(), getPlanningState: vi.fn(), getExplanation: vi.fn(), getRouteGeometry: vi.fn() };
});

import * as api from './api/client';
import { App } from './App';
import { SESSION_DATASET_KEY, useAppStore } from './store/useAppStore';
import { makeExplanation, makePlanningState } from './test/fixtures';
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

  it('opens one request edit dialog from the explanation card and closes it', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104' });
    render(<App />);
    expect(screen.queryByRole('dialog', { name: 'Изменить заявку' })).not.toBeInTheDocument();
    const card = screen.getByRole('region', { name: 'Объяснение по заявке' });
    fireEvent.click(within(card).getByRole('button', { name: 'Изменить' }));

    const dialog = screen.getByRole('dialog', { name: 'Изменить заявку' });
    expect(within(dialog).getByRole('heading', { name: 'Изменить заявку 50104' })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Указать точку на карте' }));
    expect(screen.getByText('Кликните по карте, чтобы указать новое место заявки 50104')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Отмена' }));
    expect(screen.queryByRole('dialog', { name: 'Изменить заявку' })).not.toBeInTheDocument();
    expect(useAppStore.getState()).toMatchObject({ editingRequestId: null, pickMode: false });
    expect(await screen.findByText('Подложка OpenStreetMap: ключ Яндекс Карт не задан')).toBeInTheDocument();
  });

  it('opens one delay dialog from the toolbar and from the route card of an engineer', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '14:00' });
    render(<App />);
    expect(screen.queryByRole('dialog', { name: 'Задержка инженера' })).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Задержка' }));
    const fromToolbar = screen.getByRole('dialog', { name: 'Задержка инженера' });
    expect((within(fromToolbar).getByLabelText('Инженер') as HTMLSelectElement).value).toBe('E01');
    fireEvent.click(within(fromToolbar).getByRole('button', { name: 'Отмена' }));
    expect(screen.queryByRole('dialog', { name: 'Задержка инженера' })).not.toBeInTheDocument();

    act(() => useAppStore.getState().selectEngineer('E02'));
    fireEvent.click(within(screen.getByRole('region', { name: 'Маршрут инженера' })).getByRole('button', { name: 'Задержка' }));
    expect(screen.getAllByRole('dialog', { name: 'Задержка инженера' })).toHaveLength(1);
    expect((within(screen.getByRole('dialog', { name: 'Задержка инженера' })).getByLabelText('Инженер') as HTMLSelectElement).value).toBe(
      'E02',
    );
    expect(await screen.findByText('Подложка OpenStreetMap: ключ Яндекс Карт не задан')).toBeInTheDocument();
  });

  it('moves the request edit aside while an event toolbar dialog is open', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), editingRequestId: '50104' });
    const { container } = render(<App />);
    const shell = () => container.querySelector('.app-shell');
    expect(shell()).not.toHaveClass('app-shell--toolbar-dialog');
    fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Смена транспорта' }));
    expect(screen.getByRole('dialog', { name: 'Смена транспорта' })).toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Изменить заявку' })).toBeInTheDocument();
    expect(shell()).toHaveClass('app-shell--toolbar-dialog');

    fireEvent.click(within(screen.getByRole('dialog', { name: 'Смена транспорта' })).getByRole('button', { name: 'Отмена' }));
    expect(shell()).not.toHaveClass('app-shell--toolbar-dialog');
    expect(await screen.findByText('Подложка OpenStreetMap: ключ Яндекс Карт не задан')).toBeInTheDocument();
  });

  it('names the dialog that is picking a point on the map', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), editingRequestId: '50104', pickMode: true, pickFor: 'urgent' });
    render(<App />);
    expect(screen.getByText('Кликните по карте, чтобы указать место срочной заявки')).toBeInTheDocument();
    act(() => useAppStore.getState().startPick('edit'));
    expect(screen.getByText('Кликните по карте, чтобы указать новое место заявки 50104')).toBeInTheDocument();
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
