import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    getConfig: vi.fn(),
    uploadFile: vi.fn(),
    getDatasetStatus: vi.fn(),
    buildPlan: vi.fn(),
    getPlanningState: vi.fn(),
    postEvent: vi.fn(),
  };
});

import * as api from '../api/client';
import type { DatasetStatus, PlanningState } from '../api/types';
import { cancelEvent } from '../lib/events';
import { makeDatasetStatus, makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { POLL_INTERVAL_MS, SESSION_DATASET_KEY, useAppStore } from './useAppStore';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const processing = () => makeDatasetStatus({ status: 'processing', stage: 'parsing', report: null });

beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
  resetStore();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useAppStore', () => {
  it('uploads a file and polls until the dataset is ready', async () => {
    vi.useFakeTimers();
    vi.mocked(api.uploadFile).mockResolvedValue(processing());
    vi.mocked(api.getDatasetStatus)
      .mockResolvedValueOnce(makeDatasetStatus({ status: 'processing', stage: 'matrix', report: null }))
      .mockResolvedValueOnce(makeDatasetStatus());

    const done = useAppStore.getState().upload(new File(['x'], 'east.csv'));
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(useAppStore.getState().datasetStatus?.stage).toBe('matrix');
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    await done;

    expect(useAppStore.getState()).toMatchObject({ datasetId: 'd_test', busy: false, error: null });
    expect(useAppStore.getState().datasetStatus?.status).toBe('ready');
    expect(api.getDatasetStatus).toHaveBeenCalledTimes(2);
  });

  it('retries transient status poll failures and keeps tracking the upload', async () => {
    vi.useFakeTimers();
    vi.mocked(api.uploadFile).mockResolvedValue(processing());
    vi.mocked(api.getDatasetStatus)
      .mockRejectedValueOnce(new api.ApiError(502, 'Ошибка сервера 502'))
      .mockRejectedValueOnce(new api.ApiError(0, 'Сервер недоступен. Проверьте, что backend запущен.'))
      .mockResolvedValueOnce(makeDatasetStatus());

    const done = useAppStore.getState().upload(new File(['x'], 'east.csv'));
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
    await done;

    expect(api.getDatasetStatus).toHaveBeenCalledTimes(3);
    expect(useAppStore.getState()).toMatchObject({ busy: false, error: null });
    expect(useAppStore.getState().datasetStatus?.status).toBe('ready');
  });

  it('gives up after three retries and lets the dispatcher upload again', async () => {
    vi.useFakeTimers();
    vi.mocked(api.uploadFile).mockResolvedValue(processing());
    vi.mocked(api.getDatasetStatus).mockRejectedValue(new api.ApiError(503, 'Ошибка сервера 503'));

    const done = useAppStore.getState().upload(new File(['x'], 'east.csv'));
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 5);
    await done;

    expect(api.getDatasetStatus).toHaveBeenCalledTimes(4);
    expect(useAppStore.getState()).toMatchObject({
      busy: false,
      datasetStatus: null,
      error: 'Ошибка сервера 503. Загрузите файл ещё раз.',
    });
  });

  it('stops polling at once when the dataset is gone', async () => {
    vi.useFakeTimers();
    vi.mocked(api.uploadFile).mockResolvedValue(processing());
    vi.mocked(api.getDatasetStatus).mockRejectedValue(new api.ApiError(404, 'Набор данных не найден'));

    const done = useAppStore.getState().upload(new File(['x'], 'east.csv'));
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
    await done;

    expect(api.getDatasetStatus).toHaveBeenCalledTimes(1);
    expect(useAppStore.getState()).toMatchObject({
      busy: false,
      datasetStatus: null,
      error: 'Набор данных не найден. Загрузите файл ещё раз.',
    });
  });

  it('stores the backend failure message', async () => {
    vi.mocked(api.uploadFile).mockResolvedValue(
      makeDatasetStatus({ status: 'failed', stage: 'parsing', report: null, error: 'В файле нет колонок: Адрес' }),
    );
    await useAppStore.getState().upload(new File(['x'], 'bad.csv'));
    expect(useAppStore.getState()).toMatchObject({ error: 'В файле нет колонок: Адрес', busy: false });
  });

  it('defaults event time to the middle of the day but never earlier than now', () => {
    resetStore({ datasetId: 'd_test' });
    expect(useAppStore.getState().eventTime).toBe('13:00');
    useAppStore.getState().setPlanningState(makePlanningState({ now: '00:00' }));
    expect(useAppStore.getState().eventTime).toBe('13:00');
    useAppStore.getState().setPlanningState(makePlanningState({ now: '15:20' }));
    expect(useAppStore.getState().eventTime).toBe('15:20');
  });

  it('builds a plan and keeps event time not earlier than now', async () => {
    resetStore({ datasetId: 'd_test', eventTime: '10:00' });
    vi.mocked(api.buildPlan).mockResolvedValue(makePlanningState());
    await useAppStore.getState().plan();
    expect(api.buildPlan).toHaveBeenCalledWith('d_test');
    expect(useAppStore.getState()).toMatchObject({ eventTime: '13:00', showPrevious: false, busy: false });
    expect(useAppStore.getState().state?.version).toBe(4);
  });

  it('applies an event and keeps the previous state on error', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), showPrevious: true });
    vi.mocked(api.postEvent).mockRejectedValueOnce(new api.ApiError(422, 'Время события раньше текущего'));
    expect(await useAppStore.getState().applyEvent(cancelEvent('50104', '12:00'))).toBe(false);
    expect(useAppStore.getState().error).toBe('Время события раньше текущего');
    expect(useAppStore.getState().state?.version).toBe(4);

    vi.mocked(api.postEvent).mockResolvedValueOnce(makePlanningState({ version: 5 }));
    expect(await useAppStore.getState().applyEvent(cancelEvent('50104', '13:00'))).toBe(true);
    expect(useAppStore.getState()).toMatchObject({ showPrevious: false, error: null, busy: false });
    expect(useAppStore.getState().state?.version).toBe(5);
  });

  it('ignores a plan response that arrives after «Другой файл»', async () => {
    resetStore({ datasetId: 'd_test' });
    const response = deferred<PlanningState>();
    vi.mocked(api.buildPlan).mockReturnValue(response.promise);
    const pending = useAppStore.getState().plan();
    expect(useAppStore.getState().busy).toBe(true);

    useAppStore.getState().reset();
    response.resolve(makePlanningState());
    await pending;

    expect(useAppStore.getState()).toMatchObject({ state: null, datasetId: null, busy: false });
    expect(sessionStorage.getItem(SESSION_DATASET_KEY)).toBeNull();
  });

  it('ignores an event response that arrives after a new upload started', async () => {
    resetStore({ datasetId: 'd_old', state: makePlanningState({ dataset_id: 'd_old' }) });
    const response = deferred<PlanningState>();
    vi.mocked(api.postEvent).mockReturnValue(response.promise);
    const pending = useAppStore.getState().applyEvent(cancelEvent('50104', '13:00'));

    const uploaded = deferred<DatasetStatus>();
    vi.mocked(api.uploadFile).mockReturnValue(uploaded.promise);
    const uploading = useAppStore.getState().upload(new File(['x'], 'south.csv'));

    response.resolve(makePlanningState({ dataset_id: 'd_old', version: 5 }));
    expect(await pending).toBe(false);
    expect(useAppStore.getState()).toMatchObject({ state: null, busy: true });

    uploaded.resolve(makeDatasetStatus({ dataset_id: 'd_new' }));
    await uploading;
    expect(useAppStore.getState()).toMatchObject({ state: null, datasetId: 'd_new', busy: false });
  });

  it('drops the selection of a request that no longer exists', () => {
    resetStore({ selectedRequestId: 'GONE' });
    useAppStore.getState().setPlanningState(makePlanningState());
    expect(useAppStore.getState().selectedRequestId).toBeNull();
    useAppStore.getState().selectRequest('50104');
    useAppStore.getState().setPlanningState(makePlanningState());
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
  });

  it('does not switch to the previous plan when there is none', () => {
    resetStore({ state: makePlanningState({ previous_plan: null }) });
    useAppStore.getState().setShowPrevious(true);
    expect(useAppStore.getState().showPrevious).toBe(false);
  });

  it('supports picking a point on the map', () => {
    useAppStore.getState().startPick();
    expect(useAppStore.getState().pickMode).toBe(true);
    useAppStore.getState().finishPick({ lat: 55.7, lon: 37.8 });
    expect(useAppStore.getState()).toMatchObject({ pickMode: false, pickedPoint: { lat: 55.7, lon: 37.8 } });
  });

  it('opens and closes the request edit, leaving its own map pick mode', () => {
    useAppStore.getState().startPick('edit');
    useAppStore.getState().startEdit('50104');
    expect(useAppStore.getState()).toMatchObject({ editingRequestId: '50104', pickMode: false, pickedPoint: null, pickFor: null });

    useAppStore.getState().startPick('edit');
    useAppStore.getState().finishPick({ lat: 55.7, lon: 37.8 });
    useAppStore.getState().closeEdit();
    expect(useAppStore.getState()).toMatchObject({ editingRequestId: null, pickMode: false, pickedPoint: null, pickFor: null });

    useAppStore.getState().startEdit('46393');
    useAppStore.getState().reset();
    expect(useAppStore.getState().editingRequestId).toBeNull();
  });

  it('remembers which dialog picks a point and keeps the urgent request point through the request edit', () => {
    const point = { lat: 55.71, lon: 37.8 };
    useAppStore.getState().startPick('urgent');
    expect(useAppStore.getState()).toMatchObject({ pickMode: true, pickFor: 'urgent', pickedPoint: null });
    useAppStore.getState().finishPick(point);
    expect(useAppStore.getState()).toMatchObject({ pickMode: false, pickFor: 'urgent', pickedPoint: point });

    useAppStore.getState().startEdit('50104');
    useAppStore.getState().closeEdit();
    expect(useAppStore.getState()).toMatchObject({ pickFor: 'urgent', pickedPoint: point });

    useAppStore.getState().clearPick('edit');
    expect(useAppStore.getState()).toMatchObject({ pickFor: 'urgent', pickedPoint: point });
    useAppStore.getState().clearPick('urgent');
    expect(useAppStore.getState()).toMatchObject({ pickMode: false, pickFor: null, pickedPoint: null });

    useAppStore.getState().startPick('urgent');
    useAppStore.getState().reset();
    expect(useAppStore.getState()).toMatchObject({ pickMode: false, pickFor: null });
  });

  it('opens the delay dialog for any engineer or for a chosen one and closes it', () => {
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: false, delayEngineerId: null });
    useAppStore.getState().startDelay(null);
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: true, delayEngineerId: null });
    useAppStore.getState().startDelay('E02');
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: true, delayEngineerId: 'E02' });
    useAppStore.getState().closeDelay();
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: false, delayEngineerId: null });

    useAppStore.getState().startDelay('E01');
    useAppStore.getState().reset();
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: false, delayEngineerId: null });
  });

  it('keeps only one of the delay and request edit dialogs open', () => {
    useAppStore.getState().startEdit('50104');
    useAppStore.getState().startPick('edit');
    useAppStore.getState().startDelay('E02');
    expect(useAppStore.getState()).toMatchObject({
      delayDialogOpen: true,
      delayEngineerId: 'E02',
      editingRequestId: null,
      pickMode: false,
      pickFor: null,
    });

    useAppStore.getState().startEdit('46393');
    expect(useAppStore.getState()).toMatchObject({ editingRequestId: '46393', delayDialogOpen: false, delayEngineerId: null });

    const point = { lat: 55.71, lon: 37.8 };
    useAppStore.getState().startPick('urgent');
    useAppStore.getState().finishPick(point);
    useAppStore.getState().startDelay(null);
    expect(useAppStore.getState()).toMatchObject({ pickFor: 'urgent', pickedPoint: point });
  });

  it('falls back to an offline config and keeps config on reset', async () => {
    vi.mocked(api.getConfig).mockRejectedValue(new api.ApiError(0, 'Сервер недоступен. Проверьте, что backend запущен.'));
    await useAppStore.getState().loadConfig();
    expect(useAppStore.getState().config).toEqual({ yandex_maps_api_key: null, llm_enabled: false, osrm_available: false });
    useAppStore.setState({ state: makePlanningState() });
    useAppStore.getState().reset();
    expect(useAppStore.getState().state).toBeNull();
    expect(useAppStore.getState().config).not.toBeNull();
  });
});

describe('session after a page reload', () => {
  it('remembers the dataset of the shown plan and forgets it on reset or a new upload', async () => {
    useAppStore.getState().setPlanningState(makePlanningState());
    expect(sessionStorage.getItem(SESSION_DATASET_KEY)).toBe('d_test');
    useAppStore.getState().reset();
    expect(sessionStorage.getItem(SESSION_DATASET_KEY)).toBeNull();

    useAppStore.getState().setPlanningState(makePlanningState());
    vi.mocked(api.uploadFile).mockResolvedValue(makeDatasetStatus({ dataset_id: 'd_new' }));
    await useAppStore.getState().upload(new File(['x'], 'south.csv'));
    expect(sessionStorage.getItem(SESSION_DATASET_KEY)).toBeNull();
  });

  it('restores the saved plan', async () => {
    sessionStorage.setItem(SESSION_DATASET_KEY, 'd_test');
    vi.mocked(api.getPlanningState).mockResolvedValue(makePlanningState({ version: 7 }));
    await useAppStore.getState().restoreSession();
    expect(api.getPlanningState).toHaveBeenCalledWith('d_test');
    expect(useAppStore.getState().datasetId).toBe('d_test');
    expect(useAppStore.getState().state?.version).toBe(7);
  });

  it.each([404, 409])('forgets the saved dataset and stays on upload when the backend answers %i', async (status) => {
    sessionStorage.setItem(SESSION_DATASET_KEY, 'd_gone');
    vi.mocked(api.getPlanningState).mockRejectedValue(new api.ApiError(status, 'Набор данных не найден'));
    await useAppStore.getState().restoreSession();
    expect(sessionStorage.getItem(SESSION_DATASET_KEY)).toBeNull();
    expect(useAppStore.getState()).toMatchObject({ state: null, datasetId: null, error: null, busy: false });
  });

  it('keeps the saved dataset when the server is temporarily unreachable', async () => {
    sessionStorage.setItem(SESSION_DATASET_KEY, 'd_test');
    vi.mocked(api.getPlanningState).mockRejectedValue(new api.ApiError(0, 'Сервер недоступен. Проверьте, что backend запущен.'));
    await useAppStore.getState().restoreSession();
    expect(sessionStorage.getItem(SESSION_DATASET_KEY)).toBe('d_test');
    expect(useAppStore.getState()).toMatchObject({ state: null, error: 'Сервер недоступен. Проверьте, что backend запущен.' });
  });

  it('ignores a restored plan when the dispatcher already started another upload', async () => {
    sessionStorage.setItem(SESSION_DATASET_KEY, 'd_test');
    const response = deferred<PlanningState>();
    vi.mocked(api.getPlanningState).mockReturnValue(response.promise);
    const restoring = useAppStore.getState().restoreSession();
    vi.mocked(api.uploadFile).mockResolvedValue(makeDatasetStatus({ dataset_id: 'd_new' }));
    await useAppStore.getState().upload(new File(['x'], 'south.csv'));
    response.resolve(makePlanningState());
    await restoring;
    expect(useAppStore.getState()).toMatchObject({ state: null, datasetId: 'd_new' });
  });

  it('works without a saved dataset and when session storage is blocked', async () => {
    await useAppStore.getState().restoreSession();
    expect(api.getPlanningState).not.toHaveBeenCalled();

    const blocked = () => {
      throw new DOMException('Доступ к хранилищу запрещён', 'SecurityError');
    };
    const spies = [
      vi.spyOn(Storage.prototype, 'getItem').mockImplementation(blocked),
      vi.spyOn(Storage.prototype, 'setItem').mockImplementation(blocked),
      vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(blocked),
    ];
    try {
      await expect(useAppStore.getState().restoreSession()).resolves.toBeUndefined();
      expect(() => useAppStore.getState().setPlanningState(makePlanningState())).not.toThrow();
      expect(() => useAppStore.getState().reset()).not.toThrow();
    } finally {
      spies.forEach((spy) => spy.mockRestore());
    }
  });
});
