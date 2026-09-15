import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api/client')>();
  return {
    ...actual,
    getConfig: vi.fn(),
    getPlanningState: vi.fn(),
    getExplanation: vi.fn(),
    getRouteGeometry: vi.fn(),
    getReverseGeocode: vi.fn(),
  };
});

import * as api from './api/client';
import type { ReverseGeocode } from './api/types';
import { App } from './App';
import { MAP_HEIGHT, MAP_WIDTH, stubMapContainerSize } from './components/map/leafletTestEnv';
import { SESSION_DATASET_KEY, useAppStore } from './store/useAppStore';
import { makeExplanation, makePlanningState } from './test/fixtures';
import { resetStore } from './test/store';

stubMapContainerSize();

const NO_KEY_NOTE = 'Подложка OpenStreetMap: ключ Яндекс Карт не задан';
const openDialogs = () => screen.queryAllByRole('dialog').map((dialog) => dialog.getAttribute('aria-label'));
const engineerIn = (dialog: string) => (within(screen.getByRole('dialog', { name: dialog })).getByLabelText('Инженер') as HTMLSelectElement).value;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

/** Клик по пустому месту карты OpenStreetMap, когда она нарисовала план. */
async function openMapMenu(container: HTMLElement): Promise<HTMLElement> {
  expect(await screen.findByText(NO_KEY_NOTE)).toBeInTheDocument();
  await waitFor(() => expect(container.querySelectorAll('.leaflet-marker-icon').length).toBeGreaterThan(0));
  fireEvent.click(container.querySelector('.leaflet-container') as HTMLElement, { clientX: MAP_WIDTH / 2, clientY: MAP_HEIGHT / 2 });
  return screen.findByRole('group', { name: 'Меню карты' });
}

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

  it('shows the main screen with metrics, the urgent request, tabs and the map area once a plan exists', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    render(<App />);
    expect(screen.getByText('Восток')).toBeInTheDocument();
    expect(screen.getByText('Сейчас 13:00')).toBeInTheDocument();
    expect(screen.getByText('2 из 3')).toBeInTheDocument();
    const banner = within(screen.getByRole('banner'));
    expect(banner.getAllByRole('button').map((button) => button.textContent)).toEqual(['Пересчитать с нуля', 'Другой файл', 'Срочная заявка']);
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Заявки',
      'Бригады',
      'Таймлайн',
      'Неназначенные1',
      'Сравнение',
      'Рекомендуемые изменения',
    ]);
    const bar = screen.getByRole('region', { name: 'Время дня' });
    expect(within(bar).getByRole('slider', { name: 'Текущее время' })).toBeInTheDocument();
    expect(within(bar).getByRole('button', { name: 'Запустить' })).toBeInTheDocument();
    expect(screen.getByRole('banner').contains(bar)).toBe(false);
    expect(await screen.findByText(NO_KEY_NOTE)).toBeInTheDocument();
  });

  it('opens «Инженер заболел» from the clock of the day with the busiest engineer at the clock time', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    render(<App />);
    const bar = within(screen.getByRole('region', { name: 'Время дня' }));
    fireEvent.click(bar.getByRole('button', { name: 'Добавить событие' }));
    fireEvent.click(bar.getByRole('menuitem', { name: 'Инженер заболел' }));
    expect(openDialogs()).toEqual(['Инженер недоступен']);
    expect(engineerIn('Инженер недоступен')).toBe('E01');
    expect(within(screen.getByRole('dialog', { name: 'Инженер недоступен' })).getByLabelText('Недоступен с')).toHaveValue('13:00');

    fireEvent.click(bar.getByRole('button', { name: 'Добавить событие' }));
    fireEvent.click(bar.getByRole('menuitem', { name: 'Задержка инженера' }));
    expect(openDialogs()).toEqual(['Задержка инженера']);
    expect(engineerIn('Задержка инженера')).toBe('E01');
    expect(await screen.findByText(NO_KEY_NOTE)).toBeInTheDocument();
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
    expect(await screen.findByText(NO_KEY_NOTE)).toBeInTheDocument();
  });

  it('opens the engineer dialogs from the brigade page preselected, one floating dialog at a time', async () => {
    // В 13:00 визит URG-001 (с 13:05) ещё не начат, и его можно изменить.
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Бригады' }));
    fireEvent.click(screen.getByRole('button', { name: /^Бригада Белузин/ }));
    const page = screen.getByRole('region', { name: 'Бригада' });
    expect(within(page).getByRole('heading', { name: 'Бригада Белузин' })).toBeInTheDocument();

    fireEvent.click(within(page).getByRole('button', { name: 'Смена транспорта' }));
    expect(openDialogs()).toEqual(['Смена транспорта']);
    expect(screen.getByRole('dialog', { name: 'Смена транспорта' })).toHaveClass('dialog--floating');
    expect(engineerIn('Смена транспорта')).toBe('E02');

    fireEvent.click(within(page).getByRole('button', { name: 'Задержка' }));
    expect(openDialogs()).toEqual(['Задержка инженера']);
    expect(engineerIn('Задержка инженера')).toBe('E02');

    fireEvent.click(within(page).getByRole('button', { name: 'Недоступен' }));
    expect(openDialogs()).toEqual(['Инженер недоступен']);
    expect(screen.getByRole('dialog', { name: 'Инженер недоступен' })).toHaveClass('dialog--floating');
    expect(engineerIn('Инженер недоступен')).toBe('E02');

    fireEvent.click(screen.getByRole('tab', { name: 'Заявки' }));
    const urgentRow = within(screen.getByRole('tabpanel')).getByText('URG-001').closest('li') as HTMLElement;
    fireEvent.click(within(urgentRow).getByRole('button', { name: 'Изменить' }));
    expect(openDialogs()).toEqual(['Изменить заявку']);

    fireEvent.click(within(screen.getByRole('region', { name: 'Бригада' })).getByRole('button', { name: 'Смена транспорта' }));
    expect(openDialogs()).toEqual(['Смена транспорта']);
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Смена транспорта' })).getByRole('button', { name: 'Отмена' }));
    expect(openDialogs()).toEqual([]);
    expect(await screen.findByText(NO_KEY_NOTE)).toBeInTheDocument();
  });

  it('moves the floating dialogs aside while the urgent request dialog is open', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), editingRequestId: '50104' });
    const { container } = render(<App />);
    const shell = () => container.querySelector('.app-shell');
    expect(shell()).not.toHaveClass('app-shell--toolbar-dialog');
    fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Срочная заявка' }));
    expect(openDialogs()).toEqual(['Срочная заявка', 'Изменить заявку']);
    expect(shell()).toHaveClass('app-shell--toolbar-dialog');

    fireEvent.click(within(screen.getByRole('dialog', { name: 'Срочная заявка' })).getByRole('button', { name: 'Отмена' }));
    expect(shell()).not.toHaveClass('app-shell--toolbar-dialog');
    expect(openDialogs()).toEqual(['Изменить заявку']);
    expect(await screen.findByText(NO_KEY_NOTE)).toBeInTheDocument();
  });

  it('returns from a request card to the brigade page it was opened from', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation({ request_id: 'URG-001', engineer_id: 'E02', visit: null }));
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedEngineerId: 'E02' });
    render(<App />);
    const page = screen.getByRole('region', { name: 'Бригада' });
    fireEvent.click(within(page).getByText('URG-001').closest('tr') as HTMLElement);

    const card = screen.getByRole('region', { name: 'Объяснение по заявке' });
    expect(within(card).getByRole('heading', { name: 'Заявка URG-001' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Бригада' })).not.toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: '← Бригада Белузин' }));

    expect(within(screen.getByRole('region', { name: 'Бригада' })).getByRole('heading', { name: 'Бригада Белузин' })).toBeInTheDocument();
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: null, selectedEngineerId: 'E02' });
    expect(await screen.findByText(NO_KEY_NOTE)).toBeInTheDocument();
  });

  it('names the dialog that is picking a point on the map', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), editingRequestId: '50104', pickMode: true, pickFor: 'urgent' });
    render(<App />);
    expect(screen.getByText('Кликните по карте, чтобы указать место срочной заявки')).toBeInTheDocument();
    act(() => useAppStore.getState().startPick('edit'));
    expect(screen.getByText('Кликните по карте, чтобы указать новое место заявки 50104')).toBeInTheDocument();
    expect(await screen.findByText(NO_KEY_NOTE)).toBeInTheDocument();
  });

  it('adds an urgent request from a click on the map with the address found for the point', async () => {
    const lookup = deferred<ReverseGeocode>();
    vi.mocked(api.getReverseGeocode).mockReturnValue(lookup.promise);
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    const { container } = render(<App />);
    const menu = await openMapMenu(container);
    fireEvent.click(within(menu).getByRole('button', { name: 'Добавить заявку здесь' }));

    const dialog = screen.getByRole('dialog', { name: 'Срочная заявка' });
    const point = useAppStore.getState().pickedPoint as { lat: number; lon: number };
    expect(point).not.toBeNull();
    expect(within(dialog).getByText(`Точка: ${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}`)).toBeInTheDocument();
    expect(within(dialog).getByText('Ищем адрес…')).toHaveClass('muted');
    expect(within(dialog).getByLabelText('Адрес')).toHaveValue('');
    expect(api.getReverseGeocode).toHaveBeenCalledWith(point.lat, point.lon);
    expect(screen.queryByRole('group', { name: 'Меню карты' })).not.toBeInTheDocument();

    await act(async () => lookup.resolve({ address: 'Москва, Перовская улица, 42к1', precision: 'house' }));
    expect(within(dialog).queryByText('Ищем адрес…')).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText('Адрес')).toHaveValue('Москва, Перовская улица, 42к1');
  });

  it('keeps the address the dispatcher already typed when the point comes from the map', async () => {
    vi.mocked(api.getReverseGeocode).mockResolvedValue({ address: 'Москва, Перовская улица, 42к1', precision: 'house' });
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    const { container } = render(<App />);
    fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Срочная заявка' }));
    const dialog = screen.getByRole('dialog', { name: 'Срочная заявка' });
    fireEvent.change(within(dialog).getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Юности, д. 5' } });

    const menu = await openMapMenu(container);
    fireEvent.click(within(menu).getByRole('button', { name: 'Добавить заявку здесь' }));
    await waitFor(() => expect(useAppStore.getState().urgentAddressLookup).toBe('done'));
    expect(within(dialog).getByLabelText('Адрес')).toHaveValue('Город Москва, ул.Юности, д. 5');
    expect(within(dialog).getByText(/^Точка: /)).toBeInTheDocument();
  });

  it('leaves the address empty and keeps the point when no address is found for it', async () => {
    vi.mocked(api.getReverseGeocode).mockRejectedValue(new api.ApiError(422, 'Точка вне Москвы и Московской области.'));
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    const { container } = render(<App />);
    const menu = await openMapMenu(container);
    fireEvent.click(within(menu).getByRole('button', { name: 'Добавить заявку здесь' }));
    const dialog = screen.getByRole('dialog', { name: 'Срочная заявка' });
    await waitFor(() => expect(within(dialog).queryByText('Ищем адрес…')).not.toBeInTheDocument());
    expect(within(dialog).getByLabelText('Адрес')).toHaveValue('');
    expect(within(dialog).getByText(/^Точка: /)).toBeInTheDocument();
    expect(useAppStore.getState().error).toBeNull();
  });

  it('does not label a point picked again on the map with the address of the menu point', async () => {
    const lookup = deferred<ReverseGeocode>();
    vi.mocked(api.getReverseGeocode).mockReturnValue(lookup.promise);
    const applyEvent = vi.fn().mockResolvedValue(true);
    resetStore({ datasetId: 'd_test', state: makePlanningState(), applyEvent });
    const { container } = render(<App />);
    const menu = await openMapMenu(container);
    fireEvent.click(within(menu).getByRole('button', { name: 'Добавить заявку здесь' }));
    const dialog = screen.getByRole('dialog', { name: 'Срочная заявка' });
    const menuPoint = useAppStore.getState().pickedPoint;

    fireEvent.click(within(dialog).getByRole('button', { name: 'Указать точку на карте' }));
    expect(within(dialog).queryByText('Ищем адрес…')).not.toBeInTheDocument();
    fireEvent.click(container.querySelector('.leaflet-container') as HTMLElement, { clientX: MAP_WIDTH / 4, clientY: MAP_HEIGHT / 4 });
    const picked = useAppStore.getState().pickedPoint as { lat: number; lon: number };
    expect(picked).not.toBeNull();
    expect(picked).not.toEqual(menuPoint);

    await act(async () => lookup.resolve({ address: 'Москва, Перовская улица, 42к1', precision: 'house' }));
    expect(within(dialog).getByLabelText('Адрес')).toHaveValue('');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Добавить и перепланировать' }));
    await waitFor(() => expect(applyEvent).toHaveBeenCalled());
    expect(applyEvent.mock.calls[0][0].request).toMatchObject({
      address: `Точка на карте ${picked.lat.toFixed(5)}, ${picked.lon.toFixed(5)}`,
      lat: picked.lat,
      lon: picked.lon,
    });
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
