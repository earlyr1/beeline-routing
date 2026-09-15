import { create } from 'zustand';
import {
  ApiError,
  buildPlan,
  getConfig,
  getDatasetStatus,
  getPlanningState,
  getReverseGeocode,
  postEvent,
  uploadFile,
} from '../api/client';
import type { ClientConfig, DatasetStatus, HHMM, PlanEvent, PlanningState } from '../api/types';
import type { PickedPoint } from '../lib/events';
import { isValidTime, laterTime } from '../lib/format';
import { DEFAULT_LUNCH_ENABLED, DEFAULT_WORKLOAD_LEVEL, clampWorkloadLevel, lunchEnabledOf } from '../lib/workload';

export const POLL_INTERVAL_MS = 1000;
/** Сколько раз повторить опрос статуса после сбоя сети или ответа 5xx, прежде чем сдаться. */
export const POLL_RETRIES = 3;
/** Ключ sessionStorage с набором данных открытого плана: план переживает перезагрузку страницы. */
export const SESSION_DATASET_KEY = 'routing.datasetId';
/** Время события по умолчанию для демо: середина рабочего дня, но не раньше текущего времени плана. */
export const DEFAULT_EVENT_TIME: HHMM = '13:00';

/** Диалог, для которого диспетчер указывает точку на карте. */
export type PickOwner = 'urgent' | 'edit';

/** Диалог панели событий: на панели осталась только срочная заявка. */
export type ToolbarDialog = 'urgent';

/** Диалог инженера, который открывает страница бригады. */
export type EngineerDialogKind = 'transport' | 'unavailable';

export interface EngineerDialog {
  kind: EngineerDialogKind;
  engineerId: string;
}

/** Поиск адреса по точке срочной заявки: idle — не искали, loading — ждём ответ, done — ответ пришёл. */
export type AddressLookup = 'idle' | 'loading' | 'done';

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
  /** Чья точка на карте: точку видит только диалог, который начал выбор. */
  pickFor: PickOwner | null;
  /** Заявка, открытая в диалоге «Изменить заявку»; null, когда диалог закрыт. */
  editingRequestId: string | null;
  delayDialogOpen: boolean;
  /** Инженер со страницы бригады для диалога «Задержка инженера»; null, когда диалог закрыт. */
  delayEngineerId: string | null;
  /** Диалог смены транспорта или недоступности для инженера со страницы бригады; null, когда закрыт. */
  engineerDialog: EngineerDialog | null;
  /** Открытый диалог панели событий; пока он открыт, плавающие диалоги стоят левее него. */
  toolbarDialog: ToolbarDialog | null;
  /** Точка, где открыто меню карты после клика по пустому месту; null — меню закрыто. */
  mapMenu: PickedPoint | null;
  urgentAddressLookup: AddressLookup;
  /** Адрес, найденный по точке срочной заявки; null — не нашли или не искали. */
  urgentSuggestedAddress: string | null;
  /** Нагрузка инженеров от 0 до 2: диспетчер выбирает её перед планированием, сервер возвращает уровень сессии. */
  workloadLevel: number;
  /** Обед по плану: диспетчер выбирает его вместе с нагрузкой, сервер возвращает выбор сессии. */
  lunchEnabled: boolean;
}

export interface AppActions {
  loadConfig(): Promise<void>;
  upload(file: File): Promise<void>;
  plan(): Promise<void>;
  applyEvent(event: PlanEvent): Promise<boolean>;
  /** Вернуть план, открытый до перезагрузки страницы. */
  restoreSession(): Promise<void>;
  setPlanningState(state: PlanningState): void;
  selectRequest(requestId: string | null): void;
  selectEngineer(engineerId: string | null): void;
  setTab(tabId: string): void;
  setShowPrevious(value: boolean): void;
  setEventTime(value: HHMM): void;
  /** Выбрать нагрузку инженеров для следующего расчёта плана с нуля. */
  setWorkloadLevel(level: number): void;
  /** Включить или выключить обед по плану для следующего расчёта плана с нуля. */
  setLunchEnabled(value: boolean): void;
  /** Начать выбор точки на карте для диалога-владельца. */
  startPick(owner?: PickOwner): void;
  finishPick(point: PickedPoint | null): void;
  /** Сбросить выбор точки, только если он принадлежит этому диалогу. */
  clearPick(owner: PickOwner): void;
  /** Открыть диалог изменения заявки; точка, выбранная для прежнего изменения, сбрасывается. */
  startEdit(requestId: string): void;
  closeEdit(): void;
  /** Открыть диалог задержки для инженера со страницы бригады. */
  startDelay(engineerId: string): void;
  closeDelay(): void;
  /** Открыть смену транспорта или недоступность для инженера со страницы бригады. */
  openEngineerDialog(kind: EngineerDialogKind, engineerId: string): void;
  closeEngineerDialog(): void;
  openToolbarDialog(dialog: ToolbarDialog): void;
  closeToolbarDialog(): void;
  openMapMenu(point: PickedPoint): void;
  closeMapMenu(): void;
  /** «Добавить заявку здесь»: срочная заявка с точкой из меню карты и адресом, найденным по этой точке. */
  addRequestAt(point: PickedPoint): Promise<void>;
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
  eventTime: DEFAULT_EVENT_TIME,
  busy: false,
  error: null,
  pickMode: false,
  pickedPoint: null,
  pickFor: null,
  editingRequestId: null,
  delayDialogOpen: false,
  delayEngineerId: null,
  engineerDialog: null,
  toolbarDialog: null,
  mapMenu: null,
  urgentAddressLookup: 'idle',
  urgentSuggestedAddress: null,
  workloadLevel: DEFAULT_WORKLOAD_LEVEL,
  lunchEnabled: DEFAULT_LUNCH_ENABLED,
};

/** Плавающие диалоги открываются на одном месте, поэтому открытый диалог закрывает остальные. */
const NO_FLOATING_DIALOG = {
  editingRequestId: null,
  delayDialogOpen: false,
  delayEngineerId: null,
  engineerDialog: null,
} satisfies Partial<AppData>;

const NO_ADDRESS_LOOKUP = { urgentAddressLookup: 'idle', urgentSuggestedAddress: null } satisfies Partial<AppData>;

const OFFLINE_CONFIG: ClientConfig = { yandex_maps_api_key: null, llm_enabled: false, osrm_available: false };

function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return 'Неизвестная ошибка';
}

const withPeriod = (text: string) => (/[.!?…]$/u.test(text) ? text : `${text}.`);

const isTransient = (error: unknown) => error instanceof ApiError && (error.status === 0 || error.status >= 500);

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function savedDatasetId(): string | null {
  try {
    return sessionStorage.getItem(SESSION_DATASET_KEY);
  } catch {
    return null;
  }
}

function saveDatasetId(datasetId: string | null): void {
  try {
    if (datasetId === null) sessionStorage.removeItem(SESSION_DATASET_KEY);
    else sessionStorage.setItem(SESSION_DATASET_KEY, datasetId);
  } catch {
    // Хранилище недоступно (приватный режим или запрет браузера): просто не запоминаем план.
  }
}

/** Поколение сессии: растёт при новой загрузке и при «Другой файл». Ответы прошлых поколений игнорируются. */
let generation = 0;
const isCurrent = (value: number) => value === generation;

/** Номер поиска адреса по точке: ответ на заменённую точку или для закрытого диалога не подставляется. */
let addressLookup = 0;

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
    const current = ++generation;
    saveDatasetId(null);
    set({
      busy: true,
      error: null,
      datasetId: null,
      datasetStatus: null,
      state: null,
      selectedRequestId: null,
      selectedEngineerId: null,
      ...NO_FLOATING_DIALOG,
      toolbarDialog: null,
      mapMenu: null,
      ...NO_ADDRESS_LOOKUP,
    });
    try {
      let status = await uploadFile(file);
      if (!isCurrent(current)) return;
      const datasetId = status.dataset_id;
      set({ datasetId, datasetStatus: status });
      let failures = 0;
      while (status.status === 'processing') {
        await sleep(POLL_INTERVAL_MS);
        if (!isCurrent(current)) return;
        try {
          status = await getDatasetStatus(datasetId);
        } catch (error) {
          if (!isCurrent(current)) return;
          if (isTransient(error) && failures < POLL_RETRIES) {
            failures += 1;
            continue;
          }
          // Сбрасываем статус, чтобы кнопка загрузки снова стала доступна.
          set({ datasetId: null, datasetStatus: null, error: `${withPeriod(errorMessage(error))} Загрузите файл ещё раз.` });
          return;
        }
        failures = 0;
        if (!isCurrent(current)) return;
        set({ datasetStatus: status });
      }
      if (status.status === 'failed') set({ error: status.error ?? 'Не удалось обработать файл' });
    } catch (error) {
      if (isCurrent(current)) set({ error: errorMessage(error) });
    } finally {
      if (isCurrent(current)) set({ busy: false });
    }
  },

  async plan() {
    const { datasetId, workloadLevel, lunchEnabled } = get();
    if (!datasetId) return;
    const current = generation;
    set({ busy: true, error: null });
    try {
      const state = await buildPlan(datasetId, { workload_level: workloadLevel, lunch: lunchEnabled });
      if (!isCurrent(current)) return;
      get().setPlanningState(state);
      set({ selectedRequestId: null });
    } catch (error) {
      if (isCurrent(current)) set({ error: errorMessage(error) });
    } finally {
      if (isCurrent(current)) set({ busy: false });
    }
  },

  async applyEvent(event) {
    const { datasetId } = get();
    if (!datasetId) return false;
    const current = generation;
    set({ busy: true, error: null });
    try {
      const state = await postEvent(datasetId, event);
      if (!isCurrent(current)) return false;
      get().setPlanningState(state);
      return true;
    } catch (error) {
      if (isCurrent(current)) set({ error: errorMessage(error) });
      return false;
    } finally {
      if (isCurrent(current)) set({ busy: false });
    }
  },

  async restoreSession() {
    const datasetId = savedDatasetId();
    if (!datasetId || get().state) return;
    const current = generation;
    try {
      const state = await getPlanningState(datasetId);
      if (isCurrent(current)) get().setPlanningState(state);
    } catch (error) {
      if (!isCurrent(current)) return;
      // 404: backend перезапущен и набора больше нет; 409: план ещё не построен. Остаёмся на экране загрузки.
      if (error instanceof ApiError && (error.status === 404 || error.status === 409)) saveDatasetId(null);
      else set({ error: errorMessage(error) });
    }
  },

  setPlanningState(state) {
    const { selectedRequestId, editingRequestId, eventTime } = get();
    const exists = (requestId: string | null) => requestId !== null && state.requests.some((request) => request.id === requestId);
    saveDatasetId(state.dataset_id);
    set({
      state,
      datasetId: state.dataset_id,
      showPrevious: false,
      eventTime: isValidTime(eventTime) ? laterTime(eventTime, state.now) : state.now,
      selectedRequestId: exists(selectedRequestId) ? selectedRequestId : null,
      editingRequestId: exists(editingRequestId) ? editingRequestId : null,
      // Нагрузка и обед сессии на сервере: «Пересчитать с нуля» и восстановленный план продолжают с ними.
      workloadLevel: clampWorkloadLevel(state.workload_level),
      lunchEnabled: lunchEnabledOf(state.lunch_enabled),
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

  setWorkloadLevel(level) {
    set({ workloadLevel: clampWorkloadLevel(level) });
  },

  setLunchEnabled(value) {
    set({ lunchEnabled: value });
  },

  startPick(owner) {
    // Клик по карте теперь выбирает точку, а не открывает меню, поэтому прежнее меню закрывается.
    // Новая точка срочной заявки заменяет точку из меню: адрес, найденный для прежней точки, к ней не относится.
    const newUrgentPoint = owner === 'urgent';
    if (newUrgentPoint) addressLookup += 1;
    set({ pickMode: true, pickedPoint: null, pickFor: owner ?? null, mapMenu: null, ...(newUrgentPoint ? NO_ADDRESS_LOOKUP : {}) });
  },

  finishPick(point) {
    set({ pickMode: false, pickedPoint: point });
  },

  clearPick(owner) {
    if (get().pickFor === owner) set({ pickMode: false, pickedPoint: null, pickFor: null });
  },

  startEdit(requestId) {
    set({ ...NO_FLOATING_DIALOG, editingRequestId: requestId, mapMenu: null });
    get().clearPick('edit');
  },

  closeEdit() {
    set({ editingRequestId: null });
    get().clearPick('edit');
  },

  startDelay(engineerId) {
    set({ ...NO_FLOATING_DIALOG, delayDialogOpen: true, delayEngineerId: engineerId, mapMenu: null });
    get().clearPick('edit');
  },

  closeDelay() {
    set({ delayDialogOpen: false, delayEngineerId: null });
  },

  openEngineerDialog(kind, engineerId) {
    set({ ...NO_FLOATING_DIALOG, engineerDialog: { kind, engineerId }, mapMenu: null });
    get().clearPick('edit');
  },

  closeEngineerDialog() {
    set({ engineerDialog: null });
  },

  openToolbarDialog(dialog) {
    set({ toolbarDialog: dialog, mapMenu: null });
  },

  closeToolbarDialog() {
    addressLookup += 1;
    set({ toolbarDialog: null, ...NO_ADDRESS_LOOKUP });
  },

  openMapMenu(point) {
    set({ mapMenu: point });
  },

  closeMapMenu() {
    set({ mapMenu: null });
  },

  async addRequestAt(point) {
    const lookup = ++addressLookup;
    const current = generation;
    set({
      mapMenu: null,
      toolbarDialog: 'urgent',
      pickMode: false,
      pickFor: 'urgent',
      pickedPoint: point,
      urgentAddressLookup: 'loading',
      urgentSuggestedAddress: null,
    });
    let address: string | null = null;
    try {
      address = (await getReverseGeocode(point.lat, point.lon)).address?.trim() || null;
    } catch {
      // Точка вне области, геокодер выключен или недоступен: адрес останется пустым, заявка уйдёт с точкой.
    }
    if (lookup !== addressLookup || !isCurrent(current)) return;
    set({ urgentAddressLookup: 'done', urgentSuggestedAddress: address });
  },

  clearError() {
    set({ error: null });
  },

  reset() {
    generation += 1;
    addressLookup += 1;
    saveDatasetId(null);
    // Нагрузку и обед диспетчер выбирал сам: следующий файл он планирует с тем же выбором.
    const { config, workloadLevel, lunchEnabled } = get();
    set({ ...initialAppData, config, workloadLevel, lunchEnabled });
  },
}));
