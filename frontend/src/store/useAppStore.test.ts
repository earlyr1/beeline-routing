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
    addTimelineEvent: vi.fn(),
    deleteTimelineEvent: vi.fn(),
    moveCursor: vi.fn(),
    clearTimeline: vi.fn(),
    getReverseGeocode: vi.fn(),
    getTimelineVariants: vi.fn(),
    setTimelineVariant: vi.fn(),
    getScenarios: vi.fn(),
    startScenario: vi.fn(),
  };
});

import * as api from '../api/client';
import type { DatasetStatus, EventChoice, PlanEvent, PlanningState, ReverseGeocode, TimelineItem } from '../api/types';
import { agreementEvent } from '../lib/communications';
import { cancelEvent, delayEvent, reassignEvent, transportChangeEvent, unavailableEvent } from '../lib/events';
import {
  makeDatasetStatus,
  makeEventChoice,
  makePlanningState,
  makeRequestUpdateEvent,
  makeTimeline,
  makeTimelineItem,
  makeUrgentChoice,
  makeVariantOption,
} from '../test/fixtures';
import { resetStore } from '../test/store';
import { CANCEL_UNDO_MS, LOST_SESSION_MESSAGE, PLAY_TICK_MS, POLL_INTERVAL_MS, SESSION_DATASET_KEY, useAppStore } from './useAppStore';

/** План на время дня cursor. */
const at = (cursor: string, patch: Partial<PlanningState> = {}) => makePlanningState({ cursor, ...patch });

/** Шкала дня, где событие tl_4 сервер отклонил, пока считал планы впереди. */
const withRejectedDelay = (timeline: TimelineItem[]) =>
  timeline.map((item) => (item.id === 'tl_4' ? { ...item, status: 'rejected' as const, reason: 'Инженер уже недоступен.' } : item));

const REJECTED_DELAY = 'Событие Задержка: Бригада Арташкин на 150 мин с 15:00 отклонено: Инженер уже недоступен.';

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
  localStorage.clear();
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

  it('opens a prepared region the same way as an upload: polls the dataset and remembers it', async () => {
    vi.useFakeTimers();
    vi.mocked(api.startScenario).mockResolvedValue(processing());
    vi.mocked(api.getDatasetStatus).mockResolvedValue(makeDatasetStatus());

    const done = useAppStore.getState().startScenario('east');
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    await done;

    expect(api.startScenario).toHaveBeenCalledWith('east');
    expect(useAppStore.getState()).toMatchObject({ datasetId: 'd_test', busy: false, error: null });
    expect(useAppStore.getState().datasetStatus?.status).toBe('ready');
    expect(localStorage.getItem(SESSION_DATASET_KEY)).toBe('d_test');
  });

  it('keeps the prepared regions after «Другие данные», and shows the failure of one of them', async () => {
    vi.mocked(api.getScenarios).mockResolvedValue([
      { region: 'east', title: 'Восток', requests: 66, engineers: 12, generated: false },
    ]);
    await useAppStore.getState().loadScenarios();
    vi.mocked(api.startScenario).mockRejectedValue(new api.ApiError(404, 'Регион mars не найден: такого подготовленного региона нет.'));

    await useAppStore.getState().startScenario('mars');

    expect(useAppStore.getState()).toMatchObject({
      busy: false,
      datasetId: null,
      error: 'Регион mars не найден: такого подготовленного региона нет.',
    });
    // Кнопки регионов нужны и на пустом экране загрузки: их список сброс не трогает.
    useAppStore.getState().reset();
    expect(useAppStore.getState().scenarios).toHaveLength(1);
  });

  it('stores the backend failure message', async () => {
    vi.mocked(api.uploadFile).mockResolvedValue(
      makeDatasetStatus({ status: 'failed', stage: 'parsing', report: null, error: 'В файле нет колонок: Адрес' }),
    );
    await useAppStore.getState().upload(new File(['x'], 'bad.csv'));
    expect(useAppStore.getState()).toMatchObject({ error: 'В файле нет колонок: Адрес', busy: false });
  });

  it('has no event time of its own: the clock starts at the beginning of the day', () => {
    expect(useAppStore.getState()).toMatchObject({ clock: '00:00', dragging: false, playing: false, committing: false });
    expect(useAppStore.getState()).not.toHaveProperty('eventTime');
    expect(useAppStore.getState()).not.toHaveProperty('setEventTime');
  });

  it('builds a plan, puts the clock at the start of the new day and moves the cursor there', async () => {
    resetStore({ datasetId: 'd_test', clock: '14:00' });
    vi.mocked(api.buildPlan).mockResolvedValue(at('00:00'));
    vi.mocked(api.moveCursor).mockResolvedValue(at('09:00', { version: 5 }));
    await useAppStore.getState().plan();
    expect(api.buildPlan).toHaveBeenCalledWith('d_test', { workload_level: 1, lunch: true });
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', '09:00']]);
    expect(useAppStore.getState()).toMatchObject({ clock: '09:00', busy: false, committing: false });
    expect(useAppStore.getState().state?.version).toBe(5);
  });

  it('builds the plan with the chosen workload level and lunch and takes both from every received state', async () => {
    resetStore({ datasetId: 'd_test' });
    expect(useAppStore.getState()).toMatchObject({ workloadLevel: 1, lunchEnabled: true });
    useAppStore.getState().setWorkloadLevel(0);
    useAppStore.getState().setLunchEnabled(false);
    vi.mocked(api.buildPlan).mockResolvedValue(makePlanningState({ workload_level: 0, lunch_enabled: false }));
    vi.mocked(api.moveCursor).mockResolvedValue(makePlanningState({ workload_level: 0, lunch_enabled: false, cursor: '09:00' }));
    await useAppStore.getState().plan();
    expect(api.buildPlan).toHaveBeenCalledWith('d_test', { workload_level: 0, lunch: false });
    expect(useAppStore.getState()).toMatchObject({ workloadLevel: 0, lunchEnabled: false });

    useAppStore.getState().setPlanningState(makePlanningState({ workload_level: 2, lunch_enabled: true }));
    expect(useAppStore.getState()).toMatchObject({ workloadLevel: 2, lunchEnabled: true });
  });

  it('treats a state without the lunch flag from an older backend as a day with lunch', () => {
    useAppStore.getState().setLunchEnabled(false);
    const olderState: Partial<PlanningState> = makePlanningState({ lunch_enabled: false });
    delete olderState.lunch_enabled;
    useAppStore.getState().setPlanningState(olderState as PlanningState);
    expect(useAppStore.getState().lunchEnabled).toBe(true);
  });

  it('keeps the workload level inside the scale', () => {
    useAppStore.getState().setWorkloadLevel(9);
    expect(useAppStore.getState().workloadLevel).toBe(2);
    useAppStore.getState().setWorkloadLevel(-3);
    expect(useAppStore.getState().workloadLevel).toBe(0);
  });

  it('keeps the chosen workload level and lunch after «Другие данные»', () => {
    useAppStore.getState().setPlanningState(makePlanningState({ workload_level: 2, lunch_enabled: false }));
    useAppStore.getState().reset();
    expect(useAppStore.getState()).toMatchObject({ state: null, workloadLevel: 2, lunchEnabled: false });
  });

  it('adds an event to the timeline and keeps the previous state on error', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    vi.mocked(api.addTimelineEvent).mockRejectedValueOnce(new api.ApiError(422, 'Заявка 50104 уже в работе с 12:00.'));
    expect(await useAppStore.getState().applyEvent(cancelEvent('50104', '12:00'))).toBe(false);
    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', cancelEvent('50104', '12:00'), undefined);
    expect(useAppStore.getState().error).toBe('Заявка 50104 уже в работе с 12:00.');
    expect(useAppStore.getState().state?.version).toBe(4);

    vi.mocked(api.addTimelineEvent).mockResolvedValueOnce(makePlanningState({ version: 5 }));
    expect(await useAppStore.getState().applyEvent(cancelEvent('50104', '13:00'))).toBe(true);
    expect(useAppStore.getState()).toMatchObject({ error: null, busy: false });
    expect(useAppStore.getState().state?.version).toBe(5);
    expect(api.postEvent).not.toHaveBeenCalled();
    expect(api.moveCursor).not.toHaveBeenCalled();
  });

  it('ignores a plan response that arrives after «Другие данные»', async () => {
    resetStore({ datasetId: 'd_test' });
    const response = deferred<PlanningState>();
    vi.mocked(api.buildPlan).mockReturnValue(response.promise);
    const pending = useAppStore.getState().plan();
    expect(useAppStore.getState().busy).toBe(true);

    useAppStore.getState().reset();
    response.resolve(makePlanningState());
    await pending;

    expect(useAppStore.getState()).toMatchObject({ state: null, datasetId: null, busy: false });
    expect(localStorage.getItem(SESSION_DATASET_KEY)).toBeNull();
  });

  it('ignores an event response that arrives after a new upload started', async () => {
    resetStore({ datasetId: 'd_old', state: makePlanningState({ dataset_id: 'd_old' }) });
    const response = deferred<PlanningState>();
    vi.mocked(api.addTimelineEvent).mockReturnValue(response.promise);
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

  it('opens the delay dialog for the engineer of the brigade page and closes it', () => {
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: false, delayEngineerId: null, delayTime: null });
    useAppStore.getState().startDelay('E01');
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: true, delayEngineerId: 'E01', delayTime: null });
    useAppStore.getState().startDelay('E02');
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: true, delayEngineerId: 'E02' });
    useAppStore.getState().closeDelay();
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: false, delayEngineerId: null });

    useAppStore.getState().startDelay('E01');
    useAppStore.getState().reset();
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: false, delayEngineerId: null });
  });

  it('opens the delay dialog from the request card with the brigade and the time and forgets the time on closing', () => {
    useAppStore.getState().startDelay('E01', '14:45');
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: true, delayEngineerId: 'E01', delayTime: '14:45' });
    // Со страницы бригады время снова берётся с часов.
    useAppStore.getState().startDelay('E02');
    expect(useAppStore.getState()).toMatchObject({ delayEngineerId: 'E02', delayTime: null });

    useAppStore.getState().startDelay('E01', '14:45');
    useAppStore.getState().closeDelay();
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: false, delayEngineerId: null, delayTime: null });

    // Другой плавающий диалог закрывает задержку вместе с её временем.
    useAppStore.getState().startDelay('E01', '14:45');
    useAppStore.getState().startEdit('46393');
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: false, delayEngineerId: null, delayTime: null });

    useAppStore.getState().startDelay('E01', '14:45');
    useAppStore.getState().reset();
    expect(useAppStore.getState().delayTime).toBeNull();
  });

  it('remembers the open urgent request dialog next to a floating dialog and forgets it on a new session', () => {
    useAppStore.getState().openToolbarDialog('urgent');
    useAppStore.getState().startEdit('50104');
    expect(useAppStore.getState()).toMatchObject({ toolbarDialog: 'urgent', editingRequestId: '50104' });
    useAppStore.getState().openEngineerDialog('transport', 'E01');
    expect(useAppStore.getState()).toMatchObject({ toolbarDialog: 'urgent', engineerDialog: { kind: 'transport', engineerId: 'E01' } });
    useAppStore.getState().closeToolbarDialog();
    expect(useAppStore.getState()).toMatchObject({ toolbarDialog: null, engineerDialog: { kind: 'transport', engineerId: 'E01' } });

    useAppStore.getState().openToolbarDialog('urgent');
    useAppStore.getState().reset();
    expect(useAppStore.getState().toolbarDialog).toBeNull();
  });

  it('opens the transport change and unavailability dialogs for an engineer and closes them', () => {
    expect(useAppStore.getState().engineerDialog).toBeNull();
    useAppStore.getState().openEngineerDialog('transport', 'E02');
    expect(useAppStore.getState().engineerDialog).toEqual({ kind: 'transport', engineerId: 'E02' });
    useAppStore.getState().openEngineerDialog('unavailable', 'E01');
    expect(useAppStore.getState().engineerDialog).toEqual({ kind: 'unavailable', engineerId: 'E01' });
    useAppStore.getState().closeEngineerDialog();
    expect(useAppStore.getState().engineerDialog).toBeNull();

    useAppStore.getState().openEngineerDialog('transport', 'E01');
    useAppStore.getState().reset();
    expect(useAppStore.getState().engineerDialog).toBeNull();
  });

  it('keeps only one floating dialog open: request edit, delay, transport change or unavailability', () => {
    useAppStore.getState().startEdit('50104');
    useAppStore.getState().startPick('edit');
    useAppStore.getState().openEngineerDialog('transport', 'E02');
    expect(useAppStore.getState()).toMatchObject({
      engineerDialog: { kind: 'transport', engineerId: 'E02' },
      editingRequestId: null,
      delayDialogOpen: false,
      pickMode: false,
      pickFor: null,
    });

    useAppStore.getState().startDelay('E01');
    expect(useAppStore.getState()).toMatchObject({ delayDialogOpen: true, delayEngineerId: 'E01', engineerDialog: null });

    useAppStore.getState().openEngineerDialog('unavailable', 'E01');
    expect(useAppStore.getState()).toMatchObject({ engineerDialog: { kind: 'unavailable' }, delayDialogOpen: false, delayEngineerId: null });

    useAppStore.getState().startEdit('46393');
    expect(useAppStore.getState()).toMatchObject({ editingRequestId: '46393', engineerDialog: null, delayDialogOpen: false });

    const point = { lat: 55.71, lon: 37.8 };
    useAppStore.getState().startPick('urgent');
    useAppStore.getState().finishPick(point);
    useAppStore.getState().openEngineerDialog('transport', 'E01');
    expect(useAppStore.getState()).toMatchObject({ editingRequestId: null, pickFor: 'urgent', pickedPoint: point });
  });

  it('opens the map menu at a point and closes it when a dialog opens, a pick starts or the session ends', () => {
    const point = { lat: 55.71, lon: 37.8 };
    const store = () => useAppStore.getState();
    store().openMapMenu(point);
    expect(store().mapMenu).toEqual(point);
    store().closeMapMenu();
    expect(store().mapMenu).toBeNull();

    const closers = [
      () => store().startEdit('50104'),
      () => store().startDelay('E01'),
      () => store().openEngineerDialog('transport', 'E01'),
      () => store().openEngineerDialog('unavailable', 'E01'),
      () => store().openToolbarDialog('urgent'),
      () => store().startPick('edit'),
      () => store().reset(),
    ];
    for (const close of closers) {
      store().openMapMenu(point);
      close();
      expect(store().mapMenu).toBeNull();
    }
  });

  it('adds a request at a map point: the urgent dialog gets the point and the address found for it', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    const lookup = deferred<ReverseGeocode>();
    vi.mocked(api.getReverseGeocode).mockReturnValue(lookup.promise);
    const point = { lat: 55.71, lon: 37.8 };
    useAppStore.getState().openMapMenu(point);

    const adding = useAppStore.getState().addRequestAt(point);
    expect(useAppStore.getState()).toMatchObject({
      mapMenu: null,
      toolbarDialog: 'urgent',
      pickMode: false,
      pickFor: 'urgent',
      pickedPoint: point,
      urgentAddressLookup: 'loading',
      urgentSuggestedAddress: null,
    });
    expect(api.getReverseGeocode).toHaveBeenCalledWith(55.71, 37.8);

    lookup.resolve({ address: 'Москва, Перовская улица, 42к1', precision: 'house' });
    await adding;
    expect(useAppStore.getState()).toMatchObject({ urgentAddressLookup: 'done', urgentSuggestedAddress: 'Москва, Перовская улица, 42к1' });

    useAppStore.getState().closeToolbarDialog();
    expect(useAppStore.getState()).toMatchObject({ urgentAddressLookup: 'idle', urgentSuggestedAddress: null });
  });

  it('leaves the address empty when the lookup fails or finds nothing, without an error toast', async () => {
    const point = { lat: 55.71, lon: 37.8 };
    vi.mocked(api.getReverseGeocode).mockRejectedValueOnce(new api.ApiError(422, 'Точка вне Москвы и Московской области.'));
    await useAppStore.getState().addRequestAt(point);
    expect(useAppStore.getState()).toMatchObject({ urgentAddressLookup: 'done', urgentSuggestedAddress: null, error: null, pickedPoint: point });

    vi.mocked(api.getReverseGeocode).mockResolvedValueOnce({ address: null, precision: 'none' });
    await useAppStore.getState().addRequestAt(point);
    expect(useAppStore.getState()).toMatchObject({ urgentAddressLookup: 'done', urgentSuggestedAddress: null });
  });

  it('ignores an address lookup for a point the dispatcher already replaced or a dialog already closed', async () => {
    const first = deferred<ReverseGeocode>();
    const second = deferred<ReverseGeocode>();
    vi.mocked(api.getReverseGeocode).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const near = { lat: 55.71, lon: 37.8 };
    const far = { lat: 55.76, lon: 37.62 };
    const adding = [useAppStore.getState().addRequestAt(near), useAppStore.getState().addRequestAt(far)];
    second.resolve({ address: 'Москва, Тверская улица, 7', precision: 'house' });
    first.resolve({ address: 'Москва, Ташкентская улица, 16к2', precision: 'house' });
    await Promise.all(adding);
    expect(useAppStore.getState()).toMatchObject({ pickedPoint: far, urgentSuggestedAddress: 'Москва, Тверская улица, 7' });

    const late = deferred<ReverseGeocode>();
    vi.mocked(api.getReverseGeocode).mockReturnValueOnce(late.promise);
    const pending = useAppStore.getState().addRequestAt(near);
    useAppStore.getState().closeToolbarDialog();
    late.resolve({ address: 'Москва, Ташкентская улица, 16к2', precision: 'house' });
    await pending;
    expect(useAppStore.getState()).toMatchObject({ toolbarDialog: null, urgentAddressLookup: 'idle', urgentSuggestedAddress: null });
  });

  it('forgets the address lookup of the menu point once the dispatcher picks another point for the urgent request', async () => {
    const lookup = deferred<ReverseGeocode>();
    vi.mocked(api.getReverseGeocode).mockReturnValueOnce(lookup.promise);
    const menuPoint = { lat: 55.71, lon: 37.8 };
    const picked = { lat: 55.76, lon: 37.62 };
    const adding = useAppStore.getState().addRequestAt(menuPoint);

    // Точку для изменения заявки выбирают отдельно: поиск адреса срочной заявки продолжается.
    useAppStore.getState().startPick('edit');
    expect(useAppStore.getState().urgentAddressLookup).toBe('loading');

    useAppStore.getState().startPick('urgent');
    expect(useAppStore.getState()).toMatchObject({ urgentAddressLookup: 'idle', urgentSuggestedAddress: null });
    useAppStore.getState().finishPick(picked);
    lookup.resolve({ address: 'Москва, Перовская улица, 42к1', precision: 'house' });
    await adding;
    expect(useAppStore.getState()).toMatchObject({
      toolbarDialog: 'urgent',
      pickedPoint: picked,
      urgentAddressLookup: 'idle',
      urgentSuggestedAddress: null,
    });

    vi.mocked(api.getReverseGeocode).mockResolvedValueOnce({ address: 'Москва, Тверская улица, 7', precision: 'house' });
    await useAppStore.getState().addRequestAt(menuPoint);
    expect(useAppStore.getState()).toMatchObject({ urgentAddressLookup: 'done', urgentSuggestedAddress: 'Москва, Тверская улица, 7' });
    useAppStore.getState().startPick('urgent');
    expect(useAppStore.getState()).toMatchObject({ urgentAddressLookup: 'idle', urgentSuggestedAddress: null });
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
    useAppStore.getState().startDelay('E01');
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

describe('clock of the day', () => {
  it('takes the clock from the cursor of every received state unless the dispatcher drags or plays', () => {
    useAppStore.getState().setPlanningState(at('15:20'));
    expect(useAppStore.getState().clock).toBe('15:20');
    useAppStore.setState({ dragging: true });
    useAppStore.getState().setPlanningState(at('16:00'));
    expect(useAppStore.getState().clock).toBe('15:20');
    useAppStore.setState({ dragging: false, playing: true });
    useAppStore.getState().setPlanningState(at('16:30'));
    expect(useAppStore.getState().clock).toBe('15:20');
    useAppStore.setState({ playing: false });
    useAppStore.getState().setPlanningState(at('16:30'));
    expect(useAppStore.getState().clock).toBe('16:30');
  });

  it('moves the clock locally and asks the server for the plan only on commit', async () => {
    resetStore({ datasetId: 'd_test', state: at('13:00') });
    useAppStore.getState().setClock('14:10');
    expect(useAppStore.getState().clock).toBe('14:10');
    expect(api.moveCursor).not.toHaveBeenCalled();

    vi.mocked(api.moveCursor).mockResolvedValue(at('14:10', { version: 5 }));
    await useAppStore.getState().commitClock();
    expect(api.moveCursor).toHaveBeenCalledWith('d_test', '14:10');
    expect(useAppStore.getState()).toMatchObject({ clock: '14:10', committing: false });
    expect(useAppStore.getState().state?.version).toBe(5);

    await useAppStore.getState().commitClock();
    expect(api.moveCursor).toHaveBeenCalledTimes(1);
  });

  it('sends one follow-up commit with the latest clock while a commit is in flight', async () => {
    resetStore({ datasetId: 'd_test', state: at('13:00'), clock: '14:10' });
    const first = deferred<PlanningState>();
    vi.mocked(api.moveCursor).mockReturnValueOnce(first.promise).mockResolvedValueOnce(at('14:40', { version: 6 }));
    const done = useAppStore.getState().commitClock();
    expect(useAppStore.getState().committing).toBe(true);
    await vi.waitFor(() => expect(api.moveCursor).toHaveBeenCalledTimes(1));
    for (const time of ['14:20', '14:30', '14:40']) {
      useAppStore.getState().setClock(time);
      void useAppStore.getState().commitClock();
    }
    expect(api.moveCursor).toHaveBeenCalledTimes(1);
    first.resolve(at('14:10', { version: 5 }));
    await done;
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([
      ['d_test', '14:10'],
      ['d_test', '14:40'],
    ]);
    expect(useAppStore.getState()).toMatchObject({ clock: '14:40', committing: false });
    expect(useAppStore.getState().state?.version).toBe(6);
  });

  it('keeps the clock the dispatcher moved while a commit was on its way', async () => {
    resetStore({ datasetId: 'd_test', state: at('13:00'), clock: '14:10' });
    const response = deferred<PlanningState>();
    vi.mocked(api.moveCursor).mockReturnValueOnce(response.promise);
    const done = useAppStore.getState().commitClock();
    await vi.waitFor(() => expect(api.moveCursor).toHaveBeenCalledTimes(1));
    useAppStore.getState().setClock('14:25');
    response.resolve(at('14:10', { version: 5 }));
    await done;
    expect(useAppStore.getState().clock).toBe('14:25');
    expect(api.moveCursor).toHaveBeenCalledTimes(1);
  });

  it('commits the clock before an event and sends the event only after the commit in flight', async () => {
    resetStore({ datasetId: 'd_test', state: at('13:00'), clock: '14:10' });
    const commit = deferred<PlanningState>();
    vi.mocked(api.moveCursor).mockReturnValueOnce(commit.promise);
    vi.mocked(api.addTimelineEvent).mockResolvedValue(at('14:10', { version: 6 }));
    const adding = useAppStore.getState().applyEvent(cancelEvent('50104', '14:10'));
    expect(useAppStore.getState().busy).toBe(true);
    await vi.waitFor(() => expect(api.moveCursor).toHaveBeenCalledWith('d_test', '14:10'));
    expect(api.addTimelineEvent).not.toHaveBeenCalled();

    commit.resolve(at('14:10', { version: 5 }));
    expect(await adding).toBe(true);
    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', cancelEvent('50104', '14:10'), undefined);
    expect(useAppStore.getState().state?.version).toBe(6);
    expect(useAppStore.getState()).toMatchObject({ busy: false, committing: false, clock: '14:10' });
  });

  it('rebuilds the day, stops the playback and puts the clock and the cursor at the start of the new day', async () => {
    resetStore({ datasetId: 'd_test', state: at('13:00'), clock: '14:10', playing: true });
    vi.mocked(api.buildPlan).mockResolvedValue(at('00:00', { version: 7 }));
    vi.mocked(api.moveCursor).mockResolvedValue(at('09:00', { version: 8 }));
    const planning = useAppStore.getState().plan();
    expect(useAppStore.getState().playing).toBe(false);
    await planning;
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', '09:00']]);
    expect(useAppStore.getState()).toMatchObject({ clock: '09:00', busy: false, committing: false });
    expect(useAppStore.getState().state?.version).toBe(8);
  });

  it('deletes a timeline event and shows the plan the server returns', async () => {
    const timeline = makeTimeline();
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline }) });
    vi.mocked(api.deleteTimelineEvent)
      .mockRejectedValueOnce(new api.ApiError(404, 'Событие tl_9 не найдено.'))
      .mockResolvedValueOnce(at('13:00', { version: 5, timeline: timeline.slice(1) }));
    expect(await useAppStore.getState().deleteTimelineEvent('tl_9')).toBe(false);
    expect(useAppStore.getState()).toMatchObject({ error: 'Событие tl_9 не найдено.', busy: false });

    expect(await useAppStore.getState().deleteTimelineEvent('tl_1')).toBe(true);
    expect(api.deleteTimelineEvent).toHaveBeenLastCalledWith('d_test', 'tl_1');
    expect(useAppStore.getState().state?.timeline.map((item) => item.id)).toEqual(['tl_2', 'tl_3', 'tl_4', 'tl_5']);
    expect(useAppStore.getState()).toMatchObject({ error: null, busy: false });
  });

  it('tells the dispatcher about an event the server rejected since the previous plan', () => {
    const timeline = makeTimeline();
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline }) });
    useAppStore.getState().setPlanningState(at('13:00', { version: 5, timeline: withRejectedDelay(timeline) }));
    expect(useAppStore.getState().error).toBe(REJECTED_DELAY);

    useAppStore.getState().clearError();
    useAppStore.getState().setPlanningState(at('13:00', { version: 6, timeline: withRejectedDelay(timeline) }));
    expect(useAppStore.getState().error).toBeNull();
  });

  it('remembers how many events a move applied and whether the clock went back across events', () => {
    const pending = makeTimeline().map((item) => (item.status === 'applied' ? { ...item, status: 'pending' as const } : item));
    const applied = (ids: string[]) => pending.map((item) => (ids.includes(item.id) ? { ...item, status: 'applied' as const } : item));
    useAppStore.getState().setPlanningState(at('08:00', { timeline: pending }));
    expect(useAppStore.getState().timelineMove).toEqual({ applied: 0, back: false });
    useAppStore.getState().setPlanningState(at('15:30', { timeline: applied(['tl_1', 'tl_2', 'tl_3', 'tl_4']) }));
    expect(useAppStore.getState().timelineMove).toEqual({ applied: 4, back: false });
    useAppStore.getState().setPlanningState(at('13:30', { timeline: applied(['tl_1', 'tl_2', 'tl_3']) }));
    expect(useAppStore.getState().timelineMove).toEqual({ applied: 0, back: true });
    useAppStore.getState().setPlanningState(at('15:30', { dataset_id: 'd_other', timeline: applied(['tl_1', 'tl_2', 'tl_3', 'tl_4']) }));
    expect(useAppStore.getState().timelineMove).toEqual({ applied: 0, back: false });
  });
});

describe('playback of the day', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('plays ten minutes of the day per second from the clock without asking the server', async () => {
    resetStore({ datasetId: 'd_test', state: at('13:00') });
    useAppStore.getState().play();
    expect(useAppStore.getState().playing).toBe(true);
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS);
    expect(useAppStore.getState().clock).toBe('13:01');
    await vi.advanceTimersByTimeAsync(900);
    expect(useAppStore.getState().clock).toBe('13:10');
    expect(api.moveCursor).not.toHaveBeenCalled();

    vi.mocked(api.moveCursor).mockResolvedValue(at('13:10', { version: 5 }));
    useAppStore.getState().pause();
    expect(useAppStore.getState().playing).toBe(false);
    await vi.advanceTimersByTimeAsync(1000);
    expect(useAppStore.getState().clock).toBe('13:10');
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', '13:10']]);
    expect(useAppStore.getState().state?.version).toBe(5);
  });

  it('stops at an event ahead, commits the plan at its minute and plays on once the plan arrives', async () => {
    const timeline = [
      makeTimelineItem({ id: 'tl_1', event: cancelEvent('50104', '13:02'), status: 'rejected', reason: 'Заявка уже отменена.' }),
      makeTimelineItem({ id: 'tl_2', event: cancelEvent('46393', '13:04'), status: 'pending' }),
    ];
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline }) });
    const commit = deferred<PlanningState>();
    vi.mocked(api.moveCursor).mockReturnValueOnce(commit.promise);
    useAppStore.getState().play();
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 4);
    expect(useAppStore.getState().clock).toBe('13:04');
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', '13:04']]);

    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 5);
    expect(useAppStore.getState()).toMatchObject({ clock: '13:04', playing: true, committing: true });

    const applied = timeline.map((item) => (item.id === 'tl_2' ? { ...item, status: 'applied' as const } : item));
    commit.resolve(at('13:04', { version: 5, timeline: applied }));
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 2);
    expect(useAppStore.getState()).toMatchObject({ clock: '13:06', playing: true, committing: false });
    expect(useAppStore.getState().state?.version).toBe(5);
    expect(api.moveCursor).toHaveBeenCalledTimes(1);
  });

  it('stops at the end of the day scale and commits the last minute', async () => {
    resetStore({ datasetId: 'd_test', state: at('22:55') });
    vi.mocked(api.moveCursor).mockResolvedValue(at('23:00', { version: 5 }));
    useAppStore.getState().play();
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 20);
    expect(useAppStore.getState()).toMatchObject({ clock: '23:00', playing: false, committing: false });
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', '23:00']]);
  });

  it('commits a clock the server has not seen before playing', async () => {
    resetStore({ datasetId: 'd_test', state: at('13:00'), clock: '14:00' });
    vi.mocked(api.moveCursor).mockResolvedValue(at('14:00', { version: 5 }));
    useAppStore.getState().play();
    await vi.advanceTimersByTimeAsync(0);
    expect(api.moveCursor).toHaveBeenCalledWith('d_test', '14:00');
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS);
    expect(useAppStore.getState().clock).toBe('14:01');
  });

  it('starts from the beginning of the day scale where the slider stands when the clock is earlier', async () => {
    resetStore({ datasetId: 'd_test', state: at('00:00') });
    vi.mocked(api.moveCursor).mockResolvedValue(at('09:00', { version: 5 }));
    useAppStore.getState().play();
    expect(useAppStore.getState().clock).toBe('09:00');
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS);
    expect(api.moveCursor).toHaveBeenCalledWith('d_test', '09:00');
    expect(useAppStore.getState().clock).toBe('09:01');
  });

  it('stops the playback and shows the error when a commit fails', async () => {
    const timeline = [makeTimelineItem({ id: 'tl_2', event: cancelEvent('46393', '13:02'), status: 'pending' })];
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline }) });
    vi.mocked(api.moveCursor).mockRejectedValue(new api.ApiError(0, 'Сервер недоступен. Проверьте, что backend запущен.'));
    useAppStore.getState().play();
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 5);
    expect(useAppStore.getState()).toMatchObject({
      playing: false,
      committing: false,
      clock: '13:02',
      error: 'Сервер недоступен. Проверьте, что backend запущен.',
    });
  });

  it('stops without committing when the day is rebuilt and forgets the playback on «Другие данные»', async () => {
    resetStore({ datasetId: 'd_test', state: at('13:00') });
    useAppStore.getState().play();
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 2);
    useAppStore.getState().stopPlayback();
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 5);
    expect(useAppStore.getState()).toMatchObject({ playing: false, clock: '13:02' });
    expect(api.moveCursor).not.toHaveBeenCalled();

    useAppStore.getState().play();
    useAppStore.getState().reset();
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 5);
    expect(useAppStore.getState()).toMatchObject({ playing: false, clock: '00:00', state: null });
  });
});

describe('dragging the clock', () => {
  it('pauses the playback and commits only on release', async () => {
    vi.useFakeTimers();
    resetStore({ datasetId: 'd_test', state: at('13:00') });
    useAppStore.getState().play();
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 3);
    useAppStore.getState().startDrag();
    expect(useAppStore.getState()).toMatchObject({ dragging: true, playing: false, clock: '13:03' });
    for (const time of ['14:00', '15:00', '15:30']) useAppStore.getState().setClock(time);
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 3);
    expect(useAppStore.getState().clock).toBe('15:30');
    expect(api.moveCursor).not.toHaveBeenCalled();

    vi.mocked(api.moveCursor).mockResolvedValue(at('15:30', { version: 5 }));
    useAppStore.getState().endDrag();
    expect(useAppStore.getState().dragging).toBe(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', '15:30']]);

    // Отпущенный указатель приходит и как pointerup, и как lostpointercapture: второй раз ничего не отправляется.
    useAppStore.getState().endDrag();
    await vi.advanceTimersByTimeAsync(0);
    expect(api.moveCursor).toHaveBeenCalledTimes(1);
  });
});

describe('choice of a variant for an event the plan does not survive as it is', () => {
  const awaitingState = (cursor: string) =>
    at(cursor, { pending_choice: makeEventChoice(), timeline: [makeTimelineItem({ id: 'tl_2', status: 'awaiting', variant: null, event: makeEventChoice().event })] });

  it('pauses the playing clock at the event, opens the choice and plays on after the choice', async () => {
    vi.useFakeTimers();
    useAppStore.getState().setPlanningState(at('12:58'));
    vi.mocked(api.moveCursor).mockResolvedValue(awaitingState('13:00'));
    useAppStore.setState({ clock: '12:58' });
    useAppStore.setState({ state: { ...useAppStore.getState().state!, timeline: awaitingState('13:00').timeline } });
    useAppStore.getState().play();
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 3);

    expect(useAppStore.getState()).toMatchObject({ playing: false, clock: '13:00', choiceLoading: false });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');
    expect(useAppStore.getState().resumeAfterChoice).toEqual({ time: '13:00', play: true });

    vi.mocked(api.setTimelineVariant).mockResolvedValue(at('13:00', { pending_choice: null }));
    vi.mocked(api.moveCursor).mockResolvedValue(at('13:00'));
    await useAppStore.getState().chooseVariant('stable');
    expect(api.setTimelineVariant).toHaveBeenCalledWith('d_test', 'tl_2', 'stable');
    expect(useAppStore.getState()).toMatchObject({ choice: null, resumeAfterChoice: null, playing: true });
    useAppStore.getState().stopPlayback();
  });

  it('after a slider release past the event commits to the released time once the variant is chosen', async () => {
    useAppStore.getState().setPlanningState(at('09:00'));
    vi.mocked(api.moveCursor).mockResolvedValueOnce(awaitingState('13:00')).mockResolvedValueOnce(at('17:00'));
    useAppStore.getState().startDrag();
    useAppStore.getState().setClock('17:00');
    useAppStore.getState().endDrag();
    await vi.waitFor(() => expect(useAppStore.getState().choice?.entry_id).toBe('tl_2'));
    expect(useAppStore.getState()).toMatchObject({ clock: '13:00', resumeAfterChoice: { time: '17:00', play: false } });

    vi.mocked(api.setTimelineVariant).mockResolvedValue(at('13:00'));
    await useAppStore.getState().chooseVariant('keep');
    expect(vi.mocked(api.moveCursor).mock.calls.at(-1)).toEqual(['d_test', '17:00']);
    expect(useAppStore.getState().clock).toBe('17:00');
  });

  it('closing without a choice keeps the clock at the event, and play asks again', () => {
    useAppStore.getState().setPlanningState(awaitingState('13:00'));
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');
    useAppStore.getState().closeChoice();
    expect(useAppStore.getState()).toMatchObject({ choice: null, dismissedChoice: 'tl_2', clock: '13:00' });

    // Тот же ответ сервера окно заново не открывает, а «Запустить» открывает.
    useAppStore.getState().setPlanningState(awaitingState('13:00'));
    expect(useAppStore.getState().choice).toBeNull();
    useAppStore.getState().play();
    expect(useAppStore.getState()).toMatchObject({ playing: false, dismissedChoice: null, resumeAfterChoice: { time: '13:00', play: true } });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');
  });

  it('forgets a closed choice once the clock goes back before the event: play and a forward move stop at it again', async () => {
    vi.useFakeTimers();
    const pendingAhead = awaitingState('13:00').timeline!.map((item) => ({ ...item, status: 'pending' as const }));
    const moveBack = async (time: string) => {
      vi.mocked(api.moveCursor).mockResolvedValueOnce(at(time, { timeline: pendingAhead }));
      useAppStore.getState().startDrag();
      useAppStore.getState().setClock(time);
      useAppStore.getState().endDrag();
      await vi.advanceTimersByTimeAsync(0);
    };
    useAppStore.getState().setPlanningState(awaitingState('13:00'));
    useAppStore.getState().closeChoice();
    await moveBack('12:58');
    expect(useAppStore.getState()).toMatchObject({ clock: '12:58', dismissedChoice: null });

    vi.mocked(api.moveCursor).mockResolvedValueOnce(awaitingState('13:00'));
    useAppStore.getState().play();
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 30);
    expect(useAppStore.getState()).toMatchObject({ playing: false, clock: '13:00', resumeAfterChoice: { time: '13:00', play: true } });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');

    // Отпущенный за событием ползунок тоже останавливается на нём.
    useAppStore.getState().closeChoice();
    await moveBack('12:00');
    vi.mocked(api.moveCursor).mockResolvedValueOnce(awaitingState('13:00'));
    useAppStore.getState().startDrag();
    useAppStore.getState().setClock('17:00');
    useAppStore.getState().endDrag();
    await vi.advanceTimersByTimeAsync(0);
    expect(useAppStore.getState()).toMatchObject({ clock: '13:00', resumeAfterChoice: { time: '17:00', play: false } });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');
  });

  it('after a slider release past two events at the same minute asks for both and then commits the released time', async () => {
    useAppStore.getState().setPlanningState(at('09:00'));
    const awaitingSecond = at('13:00', { pending_choice: makeEventChoice({ entry_id: 'tl_3' }) });
    vi.mocked(api.moveCursor).mockResolvedValueOnce(awaitingState('13:00')).mockResolvedValueOnce(at('17:00'));
    useAppStore.getState().startDrag();
    useAppStore.getState().setClock('17:00');
    useAppStore.getState().endDrag();
    await vi.waitFor(() => expect(useAppStore.getState().choice?.entry_id).toBe('tl_2'));

    vi.mocked(api.setTimelineVariant).mockResolvedValueOnce(awaitingSecond).mockResolvedValueOnce(at('13:00'));
    expect(await useAppStore.getState().chooseVariant('keep')).toBe(true);
    expect(useAppStore.getState()).toMatchObject({ clock: '13:00', resumeAfterChoice: { time: '17:00', play: false } });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_3');
    expect(api.moveCursor).toHaveBeenCalledTimes(1);

    await useAppStore.getState().chooseVariant('stable');
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([
      ['d_test', '17:00'],
      ['d_test', '17:00'],
    ]);
    expect(useAppStore.getState()).toMatchObject({ clock: '17:00', choice: null });
    expect(useAppStore.getState().state?.cursor).toBe('17:00');
  });

  /** Ответ сервера: время встало на событии event, и «Ничего не менять» ломает план больше пересчёта. */
  const asking = (event: PlanEvent, cursor = '12:00', entryId = 'tl_9') =>
    at(cursor, {
      pending_choice: makeEventChoice({ entry_id: entryId, event }),
      timeline: [makeTimelineItem({ id: entryId, event, status: 'awaiting', variant: null })],
    });

  it('opens no choice before the answer: whether to ask decides the result, not the type of the event', async () => {
    useAppStore.getState().setPlanningState(at('12:00'));
    const response = deferred<PlanningState>();
    vi.mocked(api.addTimelineEvent).mockReturnValue(response.promise);
    const event = unavailableEvent('E02', '12:00');
    const adding = useAppStore.getState().applyEvent(event);
    await vi.waitFor(() => expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', event, undefined));
    expect(useAppStore.getState()).toMatchObject({ busy: true, choice: null, choiceLoading: false });

    response.resolve(asking(event));
    expect(await adding).toBe(true);
    expect(useAppStore.getState()).toMatchObject({ busy: false, choiceLoading: false, clock: '12:00', resumeAfterChoice: { time: '12:00', play: false } });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_9');
  });

  it.each<[string, PlanEvent]>([
    ['an edit of a request', makeRequestUpdateEvent({ time: '12:00' })],
    ['a cancellation', cancelEvent('46393', '12:00')],
    ['a delay', delayEvent('E01', 60, '12:00')],
    ['a change of transport', transportChangeEvent('E01', 'bike', '12:00')],
    ['an unavailable engineer', unavailableEvent('E02', '12:00')],
    ['a reassignment', reassignEvent('50104', 'E02', '12:00')],
  ])('opens the choice when the answer stops the time on %s', async (_, event) => {
    useAppStore.getState().setPlanningState(at('12:00'));
    vi.mocked(api.addTimelineEvent).mockResolvedValue(asking(event));
    expect(await useAppStore.getState().applyEvent(event)).toBe(true);
    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', event, undefined);
    expect(useAppStore.getState().choice?.event).toEqual(event);
    expect(useAppStore.getState()).toMatchObject({ busy: false, playing: false, clock: '12:00' });

    // Выбор применяется, и часы остаются, где были.
    vi.mocked(api.setTimelineVariant).mockResolvedValue(at('12:00', { version: 7 }));
    expect(await useAppStore.getState().chooseVariant('optimal')).toBe(true);
    expect(api.setTimelineVariant).toHaveBeenCalledWith('d_test', 'tl_9', 'optimal');
    expect(api.moveCursor).not.toHaveBeenCalled();
    expect(useAppStore.getState()).toMatchObject({ choice: null, clock: '12:00' });
  });

  it('applies an event without a window when the server took «ничего не менять»', async () => {
    useAppStore.getState().setPlanningState(at('12:00'));
    const event = makeRequestUpdateEvent({ time: '12:00' });
    const kept = makeTimelineItem({ id: 'tl_9', event, status: 'applied', variant: 'keep', variant_auto: true });
    vi.mocked(api.addTimelineEvent).mockResolvedValue(at('12:00', { version: 6, timeline: [kept] }));
    expect(await useAppStore.getState().applyEvent(event)).toBe(true);
    expect(useAppStore.getState()).toMatchObject({ choice: null, choiceLoading: false, resumeAfterChoice: null, busy: false });
    expect(useAppStore.getState().state?.version).toBe(6);
  });

  it('shows again a choice closed earlier when an event at the clock queues behind it, but not for an event ahead', async () => {
    const waiting = awaitingState('13:00');
    useAppStore.getState().setPlanningState(waiting);
    useAppStore.getState().closeChoice();
    // Событие впереди часов ничего не ждёт: закрытое окно не всплывает.
    vi.mocked(api.addTimelineEvent).mockResolvedValue(waiting);
    expect(await useAppStore.getState().applyEvent(cancelEvent('46393', '15:00'))).toBe(true);
    expect(useAppStore.getState()).toMatchObject({ choice: null, dismissedChoice: 'tl_2' });

    // Событие на время часов само не применится, пока время стоит на прежнем: окно того события открывается снова.
    expect(await useAppStore.getState().applyEvent(cancelEvent('46393', '13:00'), 'keep')).toBe(true);
    expect(useAppStore.getState()).toMatchObject({ dismissedChoice: null, resumeAfterChoice: { time: '13:00', play: false } });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');
  });

  it('stops the playing clock on a cancellation sent after its countdown and plays on after the choice', async () => {
    vi.useFakeTimers();
    useAppStore.getState().setPlanningState(at('13:00'));
    vi.mocked(api.moveCursor).mockImplementation(async (_dataset, time) => at(time));
    const event = cancelEvent('46393', '13:00');
    useAppStore.getState().cancelRequest(event);
    useAppStore.getState().play();
    vi.mocked(api.addTimelineEvent).mockResolvedValue(asking(event, '13:00'));
    await vi.advanceTimersByTimeAsync(CANCEL_UNDO_MS);

    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', event, undefined);
    expect(useAppStore.getState()).toMatchObject({ pendingCancel: null, playing: false, clock: '13:00' });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_9');
    // Часы успели уйти вперёд, пока шёл отсчёт: после выбора они идут дальше оттуда, где стоит план.
    expect(useAppStore.getState().resumeAfterChoice?.play).toBe(true);

    vi.mocked(api.setTimelineVariant).mockResolvedValue(at('13:00'));
    await useAppStore.getState().chooseVariant('keep');
    expect(useAppStore.getState()).toMatchObject({ choice: null, playing: true });
    useAppStore.getState().stopPlayback();
  });

  it('opens the choice of an applied event from its pin and changes it without moving the clock', async () => {
    useAppStore.getState().setPlanningState(at('15:00'));
    vi.mocked(api.getTimelineVariants).mockResolvedValue(makeEventChoice({ current: 'optimal' }));
    await useAppStore.getState().openChoice('tl_2');
    expect(useAppStore.getState()).toMatchObject({ choiceLoading: false, resumeAfterChoice: null });
    expect(useAppStore.getState().choice?.current).toBe('optimal');

    vi.mocked(api.setTimelineVariant).mockResolvedValue(at('15:00', { version: 9 }));
    expect(await useAppStore.getState().chooseVariant('keep')).toBe(true);
    expect(api.moveCursor).not.toHaveBeenCalled();
    expect(useAppStore.getState()).toMatchObject({ choice: null, clock: '15:00' });
    expect(useAppStore.getState().state?.version).toBe(9);
  });

  it('computes one more variant with the request given to the chosen brigade', async () => {
    useAppStore.getState().setPlanningState(at('13:00'));
    useAppStore.setState({ choice: makeUrgentChoice() });
    const withAssign = makeUrgentChoice({ variants: [...makeUrgentChoice().variants, makeVariantOption('assign:E01')] });
    vi.mocked(api.getTimelineVariants).mockResolvedValue(withAssign);
    await useAppStore.getState().previewAssign('E01');
    expect(api.getTimelineVariants).toHaveBeenCalledWith('d_test', 'tl_3', 'E01');
    expect(useAppStore.getState().choice?.variants.map((option) => option.variant)).toEqual(['optimal', 'stable', 'keep', 'assign:E01']);
    expect(useAppStore.getState().assignLoading).toBe(false);

    // Окно закрыли, пока считали план: посчитанный вариант его не открывает заново.
    const response = deferred<EventChoice>();
    vi.mocked(api.getTimelineVariants).mockReturnValue(response.promise);
    const computing = useAppStore.getState().previewAssign('E02');
    await vi.waitFor(() => expect(useAppStore.getState().assignLoading).toBe(true));
    useAppStore.getState().closeChoice();
    response.resolve(withAssign);
    await computing;
    expect(useAppStore.getState()).toMatchObject({ choice: null, assignLoading: false });
  });

  it('keeps the dialog open with an error when the choice fails and closes it on a new day', async () => {
    useAppStore.getState().setPlanningState(awaitingState('13:00'));
    vi.mocked(api.setTimelineVariant).mockRejectedValue(new api.ApiError(409, 'Событие отклонено: Инженер E02 уже недоступен.'));
    expect(await useAppStore.getState().chooseVariant('keep')).toBe(false);
    expect(useAppStore.getState()).toMatchObject({ error: 'Событие отклонено: Инженер E02 уже недоступен.', busy: false });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');
    useAppStore.getState().reset();
    expect(useAppStore.getState()).toMatchObject({ choice: null, choiceLoading: false, resumeAfterChoice: null, dismissedChoice: null });
  });
});

describe('events being prepared on the server', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('polls until every event is prepared and merges only the timeline, keeping the view', async () => {
    const timeline = makeTimeline();
    const preparing = at('13:00', { timeline, timeline_ready: false });
    resetStore({ datasetId: 'd_test' });
    useAppStore.getState().setPlanningState(preparing);
    useAppStore.setState({ selectedRequestId: '50104' });
    vi.mocked(api.getPlanningState)
      .mockResolvedValueOnce(preparing)
      .mockResolvedValueOnce(at('13:00', { timeline: withRejectedDelay(timeline), timeline_ready: true, now: '09:00' }));

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(api.getPlanningState).toHaveBeenCalledTimes(1);
    expect(useAppStore.getState().state?.timeline_ready).toBe(false);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(api.getPlanningState).toHaveBeenCalledTimes(2);
    expect(api.getPlanningState).toHaveBeenCalledWith('d_test');
    expect(useAppStore.getState().state).toMatchObject({ timeline_ready: true, timeline: withRejectedDelay(timeline), now: '13:00' });
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: '50104', error: REJECTED_DELAY });

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
    expect(api.getPlanningState).toHaveBeenCalledTimes(2);
  });

  it('waits with the poll while the dispatcher drags the clock', async () => {
    resetStore({ datasetId: 'd_test' });
    useAppStore.getState().setPlanningState(at('13:00', { timeline_ready: false }));
    useAppStore.setState({ dragging: true });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
    expect(api.getPlanningState).not.toHaveBeenCalled();

    vi.mocked(api.getPlanningState).mockResolvedValue(at('13:00', { timeline_ready: true }));
    useAppStore.setState({ dragging: false });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(api.getPlanningState).toHaveBeenCalledTimes(1);
    expect(useAppStore.getState().state?.timeline_ready).toBe(true);
  });

  it('does not merge the timeline of another plan', async () => {
    resetStore({ datasetId: 'd_test' });
    useAppStore.getState().setPlanningState(at('13:00', { timeline_ready: false }));
    vi.mocked(api.getPlanningState).mockResolvedValue(at('14:00', { version: 9, timeline: makeTimeline(), timeline_ready: true }));
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(api.getPlanningState).toHaveBeenCalledTimes(1);
    expect(useAppStore.getState().state).toMatchObject({ version: 4, cursor: '13:00', timeline: [], timeline_ready: false });
  });
});

describe('agreed windows of the calls to clients', () => {
  /** Ответ сервера на звонок: событие «Коммуникация» применено, договорённость пришла в составе плана. */
  const answered = (event: PlanEvent): PlanningState => {
    const state = useAppStore.getState().state!;
    const agreed = { window: event.agreed_window ?? null, entry_id: 'tl_7' };
    return { ...state, agreed: { ...(state.agreed ?? {}), [event.request_id!]: agreed } };
  };
  const window = { start: '14:00', end: '16:00', asap: false };

  it('sends the call as an event at the time on the clock and shows it at once', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
    vi.mocked(api.addTimelineEvent).mockImplementation(async (_dataset, event) => answered(event));

    const sent = useAppStore.getState().markAgreed('50104');
    // Строка уходит вниз, не дожидаясь ответа: диспетчер уже положил трубку. Снять её пока нечем.
    expect(useAppStore.getState().agreed).toEqual({ '50104': { window, entry_id: null, applied: false } });
    await sent;
    // Без стратегии: нужно ли окно выбора, решит сервер, как у любого события.
    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', agreementEvent('50104', window, '13:00'), undefined);
    expect(useAppStore.getState().agreed).toEqual({ '50104': { window, entry_id: 'tl_7', applied: true } });

    // К 18754 сегодня не приедут: звонок значит, что клиенту так и сказали, и заявка переносится.
    await useAppStore.getState().markAgreed('18754');
    expect(api.addTimelineEvent).toHaveBeenLastCalledWith('d_test', agreementEvent('18754', null, '13:00'), undefined);
    expect(useAppStore.getState().agreed['18754']).toEqual({ window: null, entry_id: 'tl_7', applied: true });
  });

  it('brings the row back to the calls when the server did not take the call', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
    vi.mocked(api.addTimelineEvent).mockRejectedValue(new api.ApiError(422, 'Заявка 50104 уже в работе с 13:00, перенести её нельзя.'));

    await useAppStore.getState().markAgreed('50104');

    expect(useAppStore.getState().agreed).toEqual({});
    expect(useAppStore.getState().error).toBe('Заявка 50104 уже в работе с 13:00, перенести её нельзя.');
  });

  it('shows the marks the server sends with the plan and nothing else', () => {
    const mark = { window, entry_id: 'tl_7' };
    useAppStore.getState().setPlanningState(makePlanningState({ agreed: { '50104': mark } }));
    expect(useAppStore.getState().agreed).toEqual({ '50104': { ...mark, applied: true } });

    // Часы ушли раньше звонка, день пересчитали или сбросили события: в ответе отметки больше нет.
    useAppStore.getState().setPlanningState(at('00:00'));
    expect(useAppStore.getState().agreed).toEqual({});

    // Другой набор данных: у него свои отметки, здесь их нет.
    useAppStore.getState().setPlanningState(makePlanningState({ dataset_id: 'd_other' }));
    expect(useAppStore.getState().agreed).toEqual({});
  });

  it('keeps a mark that is still in flight when an earlier answer arrives without it', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
    let answer: (state: PlanningState) => void = () => {};
    vi.mocked(api.addTimelineEvent).mockReturnValue(new Promise<PlanningState>((resolve) => { answer = resolve; }));

    const sent = useAppStore.getState().markAgreed('50104');
    expect(useAppStore.getState().agreed['50104']).toBeDefined();
    // Пока звонок в очереди, приходит ответ запроса, стоявшего раньше него: отметки в нём ещё нет.
    useAppStore.getState().setPlanningState(makePlanningState({ version: 5 }));
    expect(useAppStore.getState().agreed['50104']).toEqual({ window, entry_id: null, applied: false });

    await vi.waitFor(() => expect(api.addTimelineEvent).toHaveBeenCalled());
    answer(makePlanningState({ version: 6, agreed: { '50104': { window, entry_id: 'tl_7' } } }));
    await sent;
    expect(useAppStore.getState().agreed['50104']).toEqual({ window, entry_id: 'tl_7', applied: true });
  });

  it('keeps the mark of a call that waits for a choice of the variant', async () => {
    // Сервер остановил часы на самом звонке: он ждёт выбора и ещё не применён, договорённости в плане нет.
    // Отметка остаётся, и снять её можно, удалив событие; повторный ✓ второй звонок на шкалу не поставит.
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
    const call = agreementEvent('50104', window, '13:00');
    const waiting = makeTimelineItem({ id: 'tl_8', event: call, status: 'awaiting', variant: null });
    vi.mocked(api.addTimelineEvent).mockResolvedValue(makePlanningState({ version: 5, cursor: '13:00', timeline: [waiting] }));

    await useAppStore.getState().markAgreed('50104');
    expect(useAppStore.getState().agreed).toEqual({ '50104': { window, entry_id: 'tl_8', applied: false } });

    // Звонок встал за чужим событием, которое ждёт выбора: он тоже уже был.
    const blocked = makeTimelineItem({ id: 'tl_9', event: { ...call, request_id: '18754', agreed_window: null }, status: 'pending' });
    const ahead = makeTimelineItem({ id: 'tl_10', event: { ...call, time: '15:00', request_id: '46393' }, status: 'pending' });
    useAppStore.getState().setPlanningState(makePlanningState({ version: 6, cursor: '13:00', timeline: [blocked, ahead] }));
    // А звонок впереди часов ещё не случился: отметки у него нет.
    expect(useAppStore.getState().agreed).toEqual({ '18754': { window: null, entry_id: 'tl_9', applied: false } });
  });

  it('takes back only its own mark when the server did not accept it', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
    let refuse: (error: unknown) => void = () => {};
    vi.mocked(api.addTimelineEvent).mockImplementation((_dataset, event) => {
      if (event.request_id === '50104') return new Promise<PlanningState>((_resolve, reject) => { refuse = reject; });
      return Promise.resolve(answered(event));
    });

    const refused = useAppStore.getState().markAgreed('50104');
    // Пока первый звонок не доехал, диспетчер отмечает соседнюю строку.
    const kept = useAppStore.getState().markAgreed('18754');
    await vi.waitFor(() => expect(api.addTimelineEvent).toHaveBeenCalled());
    refuse(new api.ApiError(503, 'Не удалось сохранить: база недоступна.'));
    await refused;

    // Снята только та отметка, которую сервер не принял: соседняя ещё стоит в очереди и не пропала.
    expect(Object.keys(useAppStore.getState().agreed)).toEqual(['18754']);
    expect(useAppStore.getState().error).toBe('Не удалось сохранить: база недоступна.');
    await kept;
    expect(useAppStore.getState().agreed).toEqual({ '18754': { window: null, entry_id: 'tl_7', applied: true } });
  });

  it('sends the cancellation of a client who refused with the chosen strategy', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
    vi.mocked(api.addTimelineEvent).mockResolvedValue(makePlanningState({ version: 5 }));
    expect(await useAppStore.getState().applyEvent(cancelEvent('50104', '13:00'), 'keep')).toBe(true);
    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', cancelEvent('50104', '13:00'), 'keep');
  });

  it.each([
    ['«Сброс событий»', () => useAppStore.getState().resetEvents(), () => vi.mocked(api.clearTimeline)],
    ['пересчёт с нуля', () => useAppStore.getState().plan(), () => vi.mocked(api.buildPlan)],
  ])('drops a cancel still in its notice on %s: the timeline is emptied anyway', async (_, run, request) => {
    vi.useFakeTimers();
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:00' });
    request().mockResolvedValue(makePlanningState({ timeline: [], cursor: '09:00' }));
    vi.mocked(api.moveCursor).mockResolvedValue(makePlanningState({ timeline: [], cursor: '09:00' }));
    useAppStore.getState().cancelRequest(cancelEvent('50104', '13:00'), 'keep');
    await run();
    expect(useAppStore.getState().pendingCancel).toBeNull();
    await vi.advanceTimersByTimeAsync(CANCEL_UNDO_MS * 2);
    expect(api.addTimelineEvent).not.toHaveBeenCalled();
  });
});

describe('session after a page reload or a closed browser', () => {
  it('remembers the dataset of the shown plan, forgets it on reset and remembers a new upload at once', async () => {
    useAppStore.getState().setPlanningState(makePlanningState());
    expect(localStorage.getItem(SESSION_DATASET_KEY)).toBe('d_test');
    useAppStore.getState().reset();
    expect(localStorage.getItem(SESSION_DATASET_KEY)).toBeNull();

    useAppStore.getState().setPlanningState(makePlanningState());
    useAppStore.getState().openEngineerDialog('unavailable', 'E01');
    useAppStore.getState().openMapMenu({ lat: 55.71, lon: 37.8 });
    vi.mocked(api.uploadFile).mockResolvedValue(makeDatasetStatus({ dataset_id: 'd_new' }));
    await useAppStore.getState().upload(new File(['x'], 'south.csv'));
    // Новый файл запоминается сразу после загрузки, ещё до готового плана.
    expect(localStorage.getItem(SESSION_DATASET_KEY)).toBe('d_new');
    expect(useAppStore.getState()).toMatchObject({ engineerDialog: null, mapMenu: null });
  });

  it('forgets an upload that failed to process', async () => {
    vi.mocked(api.uploadFile).mockResolvedValue(
      makeDatasetStatus({ status: 'failed', stage: 'parsing', report: null, error: 'В файле нет колонок: Адрес' }),
    );
    await useAppStore.getState().upload(new File(['x'], 'bad.csv'));
    expect(localStorage.getItem(SESSION_DATASET_KEY)).toBeNull();
  });

  it('restores the saved plan and marks the store as restoring until the answer', async () => {
    localStorage.setItem(SESSION_DATASET_KEY, 'd_test');
    const response = deferred<PlanningState>();
    vi.mocked(api.getPlanningState).mockReturnValue(response.promise);
    const restoring = useAppStore.getState().restoreSession();
    expect(useAppStore.getState().restoring).toBe(true);

    response.resolve(makePlanningState({ version: 7 }));
    await restoring;
    expect(api.getPlanningState).toHaveBeenCalledWith('d_test');
    expect(useAppStore.getState()).toMatchObject({ datasetId: 'd_test', restoring: false });
    expect(useAppStore.getState().state?.version).toBe(7);
  });

  it('continues the plan a tab remembered in session storage before the update and moves it to local storage', async () => {
    sessionStorage.setItem(SESSION_DATASET_KEY, 'd_test');
    vi.mocked(api.getPlanningState).mockResolvedValue(makePlanningState());
    await useAppStore.getState().restoreSession();
    expect(api.getPlanningState).toHaveBeenCalledWith('d_test');
    expect(localStorage.getItem(SESSION_DATASET_KEY)).toBe('d_test');
    expect(sessionStorage.getItem(SESSION_DATASET_KEY)).toBeNull();
    sessionStorage.clear();
  });

  it('forgets a dataset lost with a backend restart and says so', async () => {
    localStorage.setItem(SESSION_DATASET_KEY, 'd_gone');
    vi.mocked(api.getPlanningState).mockRejectedValue(new api.ApiError(404, 'Набор данных не найден'));
    await useAppStore.getState().restoreSession();
    expect(localStorage.getItem(SESSION_DATASET_KEY)).toBeNull();
    expect(useAppStore.getState()).toMatchObject({ state: null, datasetId: null, error: LOST_SESSION_MESSAGE, busy: false, restoring: false });
  });

  it('continues a file that was still processing when the browser was closed', async () => {
    vi.useFakeTimers();
    localStorage.setItem(SESSION_DATASET_KEY, 'd_test');
    vi.mocked(api.getPlanningState).mockRejectedValue(new api.ApiError(409, 'Датасет ещё обрабатывается, план не готов.'));
    vi.mocked(api.getDatasetStatus)
      .mockResolvedValueOnce(makeDatasetStatus({ status: 'processing', stage: 'matrix', report: null }))
      .mockResolvedValueOnce(makeDatasetStatus());

    const restoring = useAppStore.getState().restoreSession();
    await vi.advanceTimersByTimeAsync(0);
    expect(useAppStore.getState()).toMatchObject({ datasetId: 'd_test', busy: true, restoring: false });
    expect(useAppStore.getState().datasetStatus?.stage).toBe('matrix');

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    await restoring;
    expect(useAppStore.getState()).toMatchObject({ datasetId: 'd_test', busy: false, error: null, state: null });
    expect(useAppStore.getState().datasetStatus?.status).toBe('ready');
    expect(localStorage.getItem(SESSION_DATASET_KEY)).toBe('d_test');
  });

  it('forgets a file whose processing failed while the browser was closed', async () => {
    localStorage.setItem(SESSION_DATASET_KEY, 'd_test');
    vi.mocked(api.getPlanningState).mockRejectedValue(new api.ApiError(409, 'Предподсчёт завершился ошибкой'));
    vi.mocked(api.getDatasetStatus).mockResolvedValue(
      makeDatasetStatus({ status: 'failed', stage: 'parsing', report: null, error: 'В файле нет колонок: Адрес' }),
    );
    await useAppStore.getState().restoreSession();
    expect(localStorage.getItem(SESSION_DATASET_KEY)).toBeNull();
    expect(useAppStore.getState()).toMatchObject({ error: 'В файле нет колонок: Адрес', busy: false, restoring: false });
  });

  it('keeps the saved dataset when the server is temporarily unreachable', async () => {
    localStorage.setItem(SESSION_DATASET_KEY, 'd_test');
    vi.mocked(api.getPlanningState).mockRejectedValue(new api.ApiError(0, 'Сервер недоступен. Проверьте, что backend запущен.'));
    await useAppStore.getState().restoreSession();
    expect(localStorage.getItem(SESSION_DATASET_KEY)).toBe('d_test');
    expect(useAppStore.getState()).toMatchObject({ state: null, error: 'Сервер недоступен. Проверьте, что backend запущен.' });
  });

  it('ignores a restored plan when the dispatcher already started another upload', async () => {
    localStorage.setItem(SESSION_DATASET_KEY, 'd_test');
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
