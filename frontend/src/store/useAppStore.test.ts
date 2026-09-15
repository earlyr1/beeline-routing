import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    getConfig: vi.fn(),
    uploadFile: vi.fn(),
    getDatasetStatus: vi.fn(),
    buildPlan: vi.fn(),
    postEvent: vi.fn(),
  };
});

import * as api from '../api/client';
import { cancelEvent } from '../lib/events';
import { makeDatasetStatus, makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { POLL_INTERVAL_MS, useAppStore } from './useAppStore';

beforeEach(() => {
  vi.resetAllMocks();
  resetStore();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useAppStore', () => {
  it('uploads a file and polls until the dataset is ready', async () => {
    vi.useFakeTimers();
    vi.mocked(api.uploadFile).mockResolvedValue(makeDatasetStatus({ status: 'processing', stage: 'parsing', report: null }));
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

  it('stores the backend failure message', async () => {
    vi.mocked(api.uploadFile).mockResolvedValue(
      makeDatasetStatus({ status: 'failed', stage: 'parsing', report: null, error: 'В файле нет колонок: Адрес' }),
    );
    await useAppStore.getState().upload(new File(['x'], 'bad.csv'));
    expect(useAppStore.getState()).toMatchObject({ error: 'В файле нет колонок: Адрес', busy: false });
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
