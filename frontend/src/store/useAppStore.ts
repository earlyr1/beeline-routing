import { create } from 'zustand';
import { ApiError, buildPlan, getConfig, getDatasetStatus, postEvent, uploadFile } from '../api/client';
import type { ClientConfig, DatasetStatus, HHMM, PlanEvent, PlanningState } from '../api/types';
import type { PickedPoint } from '../lib/events';
import { isValidTime, laterTime } from '../lib/format';

export const POLL_INTERVAL_MS = 1000;

export interface AppData {
  config: ClientConfig | null;
  datasetId: string | null;
  datasetStatus: DatasetStatus | null;
  state: PlanningState | null;
  selectedRequestId: string | null;
  selectedEngineerId: string | null;
  activeTab: string;
  showPrevious: boolean;
  eventTime: HHMM;
  busy: boolean;
  error: string | null;
  pickMode: boolean;
  pickedPoint: PickedPoint | null;
}

export interface AppActions {
  loadConfig(): Promise<void>;
  upload(file: File): Promise<void>;
  plan(): Promise<void>;
  applyEvent(event: PlanEvent): Promise<boolean>;
  setPlanningState(state: PlanningState): void;
  selectRequest(requestId: string | null): void;
  selectEngineer(engineerId: string | null): void;
  setTab(tabId: string): void;
  setShowPrevious(value: boolean): void;
  setEventTime(value: HHMM): void;
  startPick(): void;
  finishPick(point: PickedPoint | null): void;
  clearError(): void;
  reset(): void;
}

export type AppState = AppData & AppActions;

export const initialAppData: AppData = {
  config: null,
  datasetId: null,
  datasetStatus: null,
  state: null,
  selectedRequestId: null,
  selectedEngineerId: null,
  activeTab: 'requests',
  showPrevious: false,
  eventTime: '00:00',
  busy: false,
  error: null,
  pickMode: false,
  pickedPoint: null,
};

const OFFLINE_CONFIG: ClientConfig = { yandex_maps_api_key: null, llm_enabled: false, osrm_available: false };

function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return 'Неизвестная ошибка';
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

let uploadSequence = 0;

export const useAppStore = create<AppState>()((set, get) => ({
  ...initialAppData,

  async loadConfig() {
    try {
      set({ config: await getConfig() });
    } catch (error) {
      set({ config: OFFLINE_CONFIG, error: errorMessage(error) });
    }
  },

  async upload(file) {
    const sequence = ++uploadSequence;
    set({
      busy: true,
      error: null,
      datasetId: null,
      datasetStatus: null,
      state: null,
      selectedRequestId: null,
      selectedEngineerId: null,
    });
    try {
      let status = await uploadFile(file);
      if (sequence !== uploadSequence) return;
      const datasetId = status.dataset_id;
      set({ datasetId, datasetStatus: status });
      while (status.status === 'processing') {
        await sleep(POLL_INTERVAL_MS);
        if (sequence !== uploadSequence) return;
        status = await getDatasetStatus(datasetId);
        if (sequence !== uploadSequence) return;
        set({ datasetStatus: status });
      }
      if (status.status === 'failed') set({ error: status.error ?? 'Не удалось обработать файл' });
    } catch (error) {
      if (sequence === uploadSequence) set({ error: errorMessage(error) });
    } finally {
      if (sequence === uploadSequence) set({ busy: false });
    }
  },

  async plan() {
    const { datasetId } = get();
    if (!datasetId) return;
    set({ busy: true, error: null });
    try {
      get().setPlanningState(await buildPlan(datasetId));
      set({ selectedRequestId: null });
    } catch (error) {
      set({ error: errorMessage(error) });
    } finally {
      set({ busy: false });
    }
  },

  async applyEvent(event) {
    const { datasetId } = get();
    if (!datasetId) return false;
    set({ busy: true, error: null });
    try {
      get().setPlanningState(await postEvent(datasetId, event));
      return true;
    } catch (error) {
      set({ error: errorMessage(error) });
      return false;
    } finally {
      set({ busy: false });
    }
  },

  setPlanningState(state) {
    const { selectedRequestId, eventTime } = get();
    const keepSelection = selectedRequestId !== null && state.requests.some((request) => request.id === selectedRequestId);
    set({
      state,
      datasetId: state.dataset_id,
      showPrevious: false,
      eventTime: isValidTime(eventTime) ? laterTime(eventTime, state.now) : state.now,
      selectedRequestId: keepSelection ? selectedRequestId : null,
    });
  },

  selectRequest(requestId) {
    set({ selectedRequestId: requestId });
  },

  selectEngineer(engineerId) {
    set({ selectedEngineerId: engineerId });
  },

  setTab(tabId) {
    set({ activeTab: tabId });
  },

  setShowPrevious(value) {
    set({ showPrevious: value && Boolean(get().state?.previous_plan) });
  },

  setEventTime(value) {
    set({ eventTime: value });
  },

  startPick() {
    set({ pickMode: true, pickedPoint: null });
  },

  finishPick(point) {
    set({ pickMode: false, pickedPoint: point });
  },

  clearError() {
    set({ error: null });
  },

  reset() {
    uploadSequence += 1;
    set({ ...initialAppData, config: get().config });
  },
}));
