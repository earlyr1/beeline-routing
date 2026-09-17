import { create } from 'zustand';
import {
  addTimelineEvent,
  ApiError,
  buildPlan,
  deleteTimelineEvent as removeTimelineEvent,
  getConfig,
  getDatasetStatus,
  getPlanningState,
  getReverseGeocode,
  moveCursor,
  uploadFile,
} from '../api/client';
import type { ClientConfig, DatasetStatus, HHMM, PlanEvent, PlanningState, TimelineItem } from '../api/types';
import type { PickedPoint } from '../lib/events';
import { fromMinutes, isValidTime, toMinutes } from '../lib/format';
import { byId } from '../lib/planView';
import { NO_TIMELINE_MOVE, newlyRejected, pausesAt, playEnd, rejectedNotice, timelineMove, type TimelineMove } from '../lib/timeBar';
import { dayScale } from '../lib/timeline';
import { DEFAULT_LUNCH_ENABLED, DEFAULT_WORKLOAD_LEVEL, clampWorkloadLevel, lunchEnabledOf } from '../lib/workload';

export const POLL_INTERVAL_MS = 1000;
/** Сколько раз повторить опрос статуса после сбоя сети или ответа 5xx, прежде чем сдаться. */
export const POLL_RETRIES = 3;
/** Ключ localStorage с набором данных открытого плана: план переживает перезагрузку страницы и закрытие браузера. */
export const SESSION_DATASET_KEY = 'routing.datasetId';
/** Шаг проигрывания дня: минута плана за 100 мс, то есть час дня за 6 секунд. */
export const PLAY_TICK_MS = 100;
/** Часы до первого ответа сервера: плана ещё нет, и шкалы дня, на начало которой их поставить, тоже. */
export const DAY_START: HHMM = '00:00';

/** Диалог, для которого диспетчер указывает точку на карте. */
export type PickOwner = 'urgent' | 'edit';

/** Диалог панели событий: на панели осталась только срочная заявка. */
export type ToolbarDialog = 'urgent';

/** Диалог инженера, который открывает страница бригады или меню «Добавить событие». */
export type EngineerDialogKind = 'transport' | 'unavailable';

export interface EngineerDialog {
  kind: EngineerDialogKind;
  /** Инженер со страницы бригады; null — диалог открыт с часов, и форма предлагает самого загруженного. */
  engineerId: string | null;
}

/** Поиск адреса по точке срочной заявки: idle — не искали, loading — ждём ответ, done — ответ пришёл. */
export type AddressLookup = 'idle' | 'loading' | 'done';

export interface AppData {
  config: ClientConfig | null;
  datasetId: string | null;
  datasetStatus: DatasetStatus | null;
  /** Открываем прежний план после перезагрузки или закрытия браузера: экран загрузки не показывается. */
  restoring: boolean;
  state: PlanningState | null;
  selectedRequestId: string | null;
  selectedEngineerId: string | null;
  /** Панель «Почему» слева от карты: подробное объяснение открытой заявки или бригады. */
  whyOpen: boolean;
  activeTab: string;
  showPrevious: boolean;
  /** Время на часах шкалы дня. Во время перетаскивания и проигрывания оно впереди плана, пока его не зафиксируют. */
  clock: HHMM;
  /** Диспетчер тянет ползунок часов: план пересчитывается, только когда он его отпустит. */
  dragging: boolean;
  /** Часы идут сами: час дня за 6 секунд. */
  playing: boolean;
  /** Сервер переводит план на время часов. */
  committing: boolean;
  /** Что сделал последний полученный план на шкале: сколько событий применилось и ушли ли часы назад через события. */
  timelineMove: TimelineMove;
  busy: boolean;
  error: string | null;
  pickMode: boolean;
  pickedPoint: PickedPoint | null;
  /** Чья точка на карте: точку видит только диалог, который начал выбор. */
  pickFor: PickOwner | null;
  /** Заявка, открытая в диалоге «Изменить заявку»; null, когда диалог закрыт. */
  editingRequestId: string | null;
  delayDialogOpen: boolean;
  /** Инженер со страницы бригады для диалога «Задержка инженера»; null — диалог закрыт или открыт с часов. */
  delayEngineerId: string | null;
  /** Диалог смены транспорта или недоступности; null, когда закрыт. */
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
  /** Поставить событие на шкалу дня. Сначала сервер переводит план на время часов, чтобы событие у часов применилось сразу. */
  applyEvent(event: PlanEvent): Promise<boolean>;
  /** Убрать событие со шкалы дня. */
  deleteTimelineEvent(entryId: string): Promise<boolean>;
  /** Вернуть план, открытый до перезагрузки страницы. */
  restoreSession(): Promise<void>;
  setPlanningState(state: PlanningState): void;
  /** Передвинуть часы только на экране: план на сервере не меняется. */
  setClock(time: HHMM): void;
  /** Попросить у сервера план на время часов; true, если план получен или уже на этом времени. */
  commitClock(): Promise<boolean>;
  /** Запустить часы: минута дня за PLAY_TICK_MS, на каждом событии впереди план пересчитывается. */
  play(): void;
  /** Остановить часы и получить план на их время. */
  pause(): void;
  /** Остановить часы без запроса к серверу: план сейчас заменит ответ другого запроса. */
  stopPlayback(): void;
  /** Диспетчер взялся за ползунок часов. */
  startDrag(): void;
  /** Диспетчер отпустил ползунок: план пересчитывается на время часов. */
  endDrag(): void;
  selectRequest(requestId: string | null): void;
  selectEngineer(engineerId: string | null): void;
  /** Открыть страницу бригады на месте карточки заявки одним шагом: панель «Почему» переходит к бригаде. */
  openBrigade(engineerId: string): void;
  /** Открыть или закрыть панель «Почему» для того, что открыто справа. */
  toggleWhy(): void;
  closeWhy(): void;
  setTab(tabId: string): void;
  setShowPrevious(value: boolean): void;
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
  /** Открыть диалог задержки для инженера со страницы бригады или, с null, для самого загруженного. */
  startDelay(engineerId: string | null): void;
  closeDelay(): void;
  /** Открыть смену транспорта или недоступность для инженера со страницы бригады или, с null, для самого загруженного. */
  openEngineerDialog(kind: EngineerDialogKind, engineerId: string | null): void;
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
  restoring: false,
  state: null,
  selectedRequestId: null,
  selectedEngineerId: null,
  whyOpen: false,
  activeTab: 'requests',
  showPrevious: false,
  clock: DAY_START,
  dragging: false,
  playing: false,
  committing: false,
  timelineMove: NO_TIMELINE_MOVE,
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

/** Новая сессия начинается с остановленными часами. */
const NO_CLOCK_ACTIVITY = { dragging: false, playing: false, committing: false } satisfies Partial<AppData>;

const OFFLINE_CONFIG: ClientConfig = { yandex_maps_api_key: null, llm_enabled: false, osrm_available: false };

function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return 'Неизвестная ошибка';
}

const withPeriod = (text: string) => (/[.!?…]$/u.test(text) ? text : `${text}.`);

const isTransient = (error: unknown) => error instanceof ApiError && (error.status === 0 || error.status >= 500);

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Прежний план пропал: backend перезапускали, а наборы данных живут в его памяти. */
export const LOST_SESSION_MESSAGE = 'Прежний план недоступен: сервис перезапускался. Загрузите файл заново.';

function savedDatasetId(): string | null {
  try {
    return localStorage.getItem(SESSION_DATASET_KEY);
  } catch {
    return null;
  }
}

function saveDatasetId(datasetId: string | null): void {
  try {
    if (datasetId === null) localStorage.removeItem(SESSION_DATASET_KEY);
    else localStorage.setItem(SESSION_DATASET_KEY, datasetId);
  } catch {
    // Хранилище недоступно (приватный режим или запрет браузера): просто не запоминаем план.
  }
}

/** Сообщение об отклонённых событиях шкалы для всплывающей ошибки. */
function rejectedMessage(items: TimelineItem[], state: PlanningState): string {
  const engineers = byId(state.engineers);
  return items.map((item) => rejectedNotice(item, engineers)).join(' ');
}

/** Поколение сессии: растёт при новой загрузке и при «Другой файл». Ответы прошлых поколений игнорируются. */
let generation = 0;
const isCurrent = (value: number) => value === generation;

/** Номер поиска адреса по точке: ответ на заменённую точку или для закрытого диалога не подставляется. */
let addressLookup = 0;

/** Запрос ждал очереди, а диспетчер уже открыл другой файл: такой запрос не отправляется. */
class StaleSession extends Error {}

/**
 * Очередь запросов, в ответ на которые сервер присылает план: часы, события шкалы и пересчёт с нуля.
 * Запросы уходят по одному, поэтому ответ медленного запроса не затирает план, полученный после него.
 */
interface RequestLane {
  tail: Promise<unknown>;
  size: number;
}

let lane: RequestLane = { tail: Promise.resolve(), size: 0 };

function enqueue<T>(current: number, send: () => Promise<T>): Promise<T> {
  if (!isCurrent(current)) return Promise.reject(new StaleSession());
  const own = lane;
  own.size += 1;
  const result = own.tail.then(() => {
    if (!isCurrent(current)) throw new StaleSession();
    return send();
  });
  own.tail = result.then(
    () => {
      own.size -= 1;
    },
    () => {
      own.size -= 1;
    },
  );
  return result;
}

/** Фиксация часов в пути: пока она идёт, новые фиксации только просят досылку с последним временем. */
let commitRun: Promise<boolean> | null = null;
let commitAgain = false;
let playTimer: ReturnType<typeof setInterval> | null = null;
let pollTimer: ReturnType<typeof setTimeout> | null = null;
/** Поколение, чей опрос состояния сейчас ждёт ответа; -1 — никакой. */
let pollInFlight = -1;

function stopTicking(): void {
  if (playTimer !== null) clearInterval(playTimer);
  playTimer = null;
}

/**
 * Забыть всё, что относится к открытому плану, кроме данных стора: таймеры часов и опроса, очередь запросов,
 * фиксацию в пути. Ответы на прежние запросы после этого игнорируются.
 */
export function resetAppSession(): void {
  generation += 1;
  addressLookup += 1;
  lane = { tail: Promise.resolve(), size: 0 };
  commitRun = null;
  commitAgain = false;
  stopTicking();
  if (pollTimer !== null) clearTimeout(pollTimer);
  pollTimer = null;
  pollInFlight = -1;
}

export const useAppStore = create<AppState>()((set, get) => {
  /** План с сервера. syncClock ставит часы на его время; фиксация часов их не трогает, диспетчер мог сдвинуть их дальше. */
  function receive(next: PlanningState, syncClock: boolean): void {
    const { state: shown, selectedRequestId, selectedEngineerId, editingRequestId, whyOpen, clock } = get();
    const same = shown !== null && shown.dataset_id === next.dataset_id ? shown : null;
    const exists = (requestId: string | null) => requestId !== null && next.requests.some((request) => request.id === requestId);
    const rejected = same ? newlyRejected(same.timeline ?? [], next.timeline ?? []) : [];
    saveDatasetId(next.dataset_id);
    set({
      state: next,
      datasetId: next.dataset_id,
      showPrevious: false,
      clock: syncClock && isValidTime(next.cursor ?? '') ? next.cursor : clock,
      timelineMove: same ? timelineMove(same, next) : NO_TIMELINE_MOVE,
      selectedRequestId: exists(selectedRequestId) ? selectedRequestId : null,
      // Заявка пропала из плана, а бригада не выбрана: объяснять в панели «Почему» больше нечего.
      whyOpen: whyOpen && (exists(selectedRequestId) || selectedEngineerId !== null),
      editingRequestId: exists(editingRequestId) ? editingRequestId : null,
      // Нагрузка и обед сессии на сервере: «Пересчитать с нуля» и восстановленный план продолжают с ними.
      workloadLevel: clampWorkloadLevel(next.workload_level),
      lunchEnabled: lunchEnabledOf(next.lunch_enabled),
      ...(rejected.length > 0 ? { error: rejectedMessage(rejected, next) } : {}),
    });
    if (next.timeline_ready === false) schedulePoll();
  }

  /** Пока сервер считает планы после событий шкалы, их статусы спрашиваются раз в POLL_INTERVAL_MS. */
  /**
   * Опрос статуса загруженного файла до готовности. Сбой обработки и потерянный набор забываются, чтобы следующее
   * открытие страницы не пыталось их вернуть.
   */
  async function followUpload(datasetId: string, first: DatasetStatus, current: number): Promise<void> {
    let status = first;
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
        saveDatasetId(null);
        set({ datasetId: null, datasetStatus: null, error: `${withPeriod(errorMessage(error))} Загрузите файл ещё раз.` });
        return;
      }
      failures = 0;
      if (!isCurrent(current)) return;
      set({ datasetStatus: status });
    }
    if (status.status === 'failed') {
      saveDatasetId(null);
      set({ error: status.error ?? 'Не удалось обработать файл' });
    }
  }

  /** Браузер закрыли, пока файл обрабатывался: показываем экран загрузки с тем же статусом и ждём готовности. */
  async function resumeUpload(datasetId: string, current: number): Promise<void> {
    let status: DatasetStatus;
    try {
      status = await getDatasetStatus(datasetId);
    } catch (error) {
      if (!isCurrent(current)) return;
      if (error instanceof ApiError && error.status === 404) {
        saveDatasetId(null);
        set({ error: LOST_SESSION_MESSAGE });
      } else {
        set({ error: errorMessage(error) });
      }
      return;
    }
    if (!isCurrent(current)) return;
    set({ datasetId, datasetStatus: status, busy: status.status === 'processing', restoring: false });
    try {
      await followUpload(datasetId, status, current);
    } finally {
      if (isCurrent(current)) set({ busy: false });
    }
  }

  function schedulePoll(): void {
    if (pollTimer !== null || pollInFlight === generation) return;
    const current = generation;
    pollTimer = setTimeout(() => {
      pollTimer = null;
      void pollTimeline(current);
    }, POLL_INTERVAL_MS);
  }

  async function pollTimeline(current: number): Promise<void> {
    const { datasetId, state, dragging, playing, committing, busy } = get();
    if (!isCurrent(current) || !datasetId || state?.timeline_ready !== false) return;
    // Пока диспетчер тянет часы или ждёт план, опрос не мешает: следующий ответ сам принесёт статусы.
    if (dragging || playing || committing || busy) {
      schedulePoll();
      return;
    }
    pollInFlight = current;
    try {
      const fresh = await getPlanningState(datasetId);
      const shown = get().state;
      if (!isCurrent(current) || !fresh || !shown) return;
      // Берём только статусы шкалы и готовность: план, выбор и «До события» на экране не сбрасываются.
      if (fresh.dataset_id === shown.dataset_id && fresh.version === shown.version && fresh.cursor === shown.cursor) {
        const rejected = newlyRejected(shown.timeline ?? [], fresh.timeline ?? []);
        set({
          state: { ...shown, timeline: fresh.timeline, timeline_ready: fresh.timeline_ready },
          ...(rejected.length > 0 ? { error: rejectedMessage(rejected, shown) } : {}),
        });
      }
    } catch {
      // Временный сбой опроса: спросим ещё раз.
    } finally {
      if (pollInFlight === current) pollInFlight = -1;
    }
    if (isCurrent(current) && get().state?.timeline_ready === false) schedulePoll();
  }

  function startTicking(): void {
    stopTicking();
    const current = generation;
    playTimer = setInterval(() => tick(current), PLAY_TICK_MS);
  }

  /** Продолжить часы после фиксации, если диспетчер их не остановил; сбой фиксации останавливает часы. */
  function resumeAfter(commit: Promise<boolean>, current: number): void {
    void commit.then((ok) => {
      if (!isCurrent(current) || !get().playing) return;
      if (ok) startTicking();
      else set({ playing: false });
    });
  }

  function finishPlayback(): void {
    stopTicking();
    set({ playing: false });
    void get().commitClock();
  }

  function tick(current: number): void {
    const { state, clock, playing } = get();
    if (!isCurrent(current) || !state || !playing) {
      stopTicking();
      return;
    }
    const end = playEnd(dayScale(state, state.plan));
    const next = toMinutes(clock) + 1;
    if (next > end) {
      finishPlayback();
      return;
    }
    set({ clock: fromMinutes(next) });
    if (pausesAt(state.timeline ?? [], next)) {
      // На минуте события часы ждут план с этим событием, а потом идут дальше.
      stopTicking();
      resumeAfter(get().commitClock(), current);
    } else if (next >= end) {
      finishPlayback();
    }
  }

  return {
    ...initialAppData,
    // Сохранённый план откроется сразу: до первого ответа сервера экран загрузки не мелькает.
    restoring: savedDatasetId() !== null,

    async loadConfig() {
      try {
        set({ config: await getConfig() });
      } catch (error) {
        set({ config: OFFLINE_CONFIG, error: errorMessage(error) });
      }
    },

    async upload(file) {
      resetAppSession();
      const current = generation;
      saveDatasetId(null);
      set({
        busy: true,
        error: null,
        datasetId: null,
        datasetStatus: null,
        restoring: false,
        state: null,
        selectedRequestId: null,
        selectedEngineerId: null,
        ...NO_FLOATING_DIALOG,
        ...NO_CLOCK_ACTIVITY,
        toolbarDialog: null,
        mapMenu: null,
        ...NO_ADDRESS_LOOKUP,
      });
      try {
        const status = await uploadFile(file);
        if (!isCurrent(current)) return;
        const datasetId = status.dataset_id;
        // Набор запоминается сразу: закрытый во время расчёта браузер при следующем открытии дождётся готовности.
        saveDatasetId(datasetId);
        set({ datasetId, datasetStatus: status });
        await followUpload(datasetId, status, current);
      } catch (error) {
        if (isCurrent(current)) set({ error: errorMessage(error) });
      } finally {
        if (isCurrent(current)) set({ busy: false });
      }
    },

    async plan() {
      const { datasetId, workloadLevel, lunchEnabled } = get();
      if (!datasetId) return;
      // Пересчёт с нуля ставит часы на начало дня: идущие часы останавливаются без фиксации.
      get().stopPlayback();
      const current = generation;
      set({ busy: true, error: null });
      try {
        const state = await enqueue(current, () => buildPlan(datasetId, { workload_level: workloadLevel, lunch: lunchEnabled }));
        if (!isCurrent(current)) return;
        get().setPlanningState(state);
        // Новый день начинается со своего начала: часы встают на начало шкалы дня, и это время уходит на сервер,
        // чтобы курсор плана совпал с часами. Шкала дня после пересчёта пуста, и перевод курсора ничего не считает.
        set({ selectedRequestId: null, clock: fromMinutes(dayScale(state, state.plan).from) });
        await get().commitClock();
      } catch (error) {
        if (isCurrent(current) && !(error instanceof StaleSession)) set({ error: errorMessage(error) });
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
        // Диспетчер видит на часах их время и ждёт, что событие в это время применится сразу.
        await get().commitClock();
        const state = await enqueue(current, () => addTimelineEvent(datasetId, event));
        if (!isCurrent(current)) return false;
        get().setPlanningState(state);
        return true;
      } catch (error) {
        if (isCurrent(current) && !(error instanceof StaleSession)) set({ error: errorMessage(error) });
        return false;
      } finally {
        if (isCurrent(current)) set({ busy: false });
      }
    },

    async deleteTimelineEvent(entryId) {
      const { datasetId } = get();
      if (!datasetId) return false;
      const current = generation;
      set({ busy: true, error: null });
      try {
        const state = await enqueue(current, () => removeTimelineEvent(datasetId, entryId));
        if (!isCurrent(current)) return false;
        get().setPlanningState(state);
        return true;
      } catch (error) {
        if (isCurrent(current) && !(error instanceof StaleSession)) set({ error: errorMessage(error) });
        return false;
      } finally {
        if (isCurrent(current)) set({ busy: false });
      }
    },

    async restoreSession() {
      const datasetId = savedDatasetId();
      if (!datasetId || get().state) {
        if (get().restoring) set({ restoring: false });
        return;
      }
      const current = generation;
      set({ restoring: true });
      try {
        const state = await getPlanningState(datasetId);
        if (isCurrent(current)) get().setPlanningState(state);
      } catch (error) {
        if (!isCurrent(current)) return;
        if (error instanceof ApiError && error.status === 404) {
          // Backend перезапущен, набора в его памяти больше нет.
          saveDatasetId(null);
          set({ error: LOST_SESSION_MESSAGE });
        } else if (error instanceof ApiError && error.status === 409) {
          // Файл ещё обрабатывается или обработка упала: возвращаемся к экрану загрузки с его статусом.
          await resumeUpload(datasetId, current);
        } else {
          set({ error: errorMessage(error) });
        }
      } finally {
        if (isCurrent(current)) set({ restoring: false });
      }
    },

    setPlanningState(state) {
      const { dragging, playing } = get();
      // Пока диспетчер тянет ползунок или часы идут, часы на экране главнее времени плана.
      receive(state, !dragging && !playing);
    },

    setClock(time) {
      set({ clock: time });
    },

    commitClock() {
      const { datasetId, state, clock } = get();
      if (!datasetId || !state) return Promise.resolve(true);
      if (commitRun) {
        // Фиксация уже в пути: когда она вернётся, досылается одна фиксация с последним временем часов.
        commitAgain = true;
        return commitRun;
      }
      if (clock === state.cursor && lane.size === 0) return Promise.resolve(true);
      const current = generation;
      set({ committing: true });
      const run: Promise<boolean> = enqueue(current, async () => {
        do {
          commitAgain = false;
          const shown = get().state;
          const target = get().clock;
          if (!shown || target === shown.cursor) break;
          const next = await moveCursor(datasetId, target);
          if (!isCurrent(current)) throw new StaleSession();
          receive(next, false);
        } while (commitAgain);
        return true;
      })
        .catch((error: unknown) => {
          if (isCurrent(current) && !(error instanceof StaleSession)) set({ error: errorMessage(error) });
          return false;
        })
        .finally(() => {
          if (commitRun === run) commitRun = null;
          if (isCurrent(current)) set({ committing: false });
        });
      commitRun = run;
      return run;
    },

    play() {
      const { state, playing, clock } = get();
      if (!state || playing) return;
      const scale = dayScale(state, state.plan);
      const end = playEnd(scale);
      // Часы раньше начала шкалы стоят на ползунке в её начале: проигрывание идёт с того места, где ползунок.
      const start = Math.max(isValidTime(clock) ? toMinutes(clock) : scale.from, scale.from);
      if (start >= end) return;
      set({ playing: true, showPrevious: false, clock: fromMinutes(start) });
      // Часы, которые сервер ещё не видел, сначала фиксируются: иначе события между планом и часами не применятся.
      resumeAfter(get().commitClock(), generation);
    },

    pause() {
      stopTicking();
      set({ playing: false });
      void get().commitClock();
    },

    stopPlayback() {
      stopTicking();
      set({ playing: false });
    },

    startDrag() {
      stopTicking();
      // Перетаскивание показывает план после событий: ответ фиксации всё равно переключил бы «До события» посреди жеста.
      set({ dragging: true, playing: false, showPrevious: false });
    },

    endDrag() {
      set({ dragging: false });
      void get().commitClock();
    },

    selectRequest(requestId) {
      // Панель «Почему» переходит к тому, что открыто справа, и закрывается, когда справа ничего не открыто.
      set({ selectedRequestId: requestId, whyOpen: get().whyOpen && (requestId !== null || get().selectedEngineerId !== null) });
    },

    selectEngineer(engineerId) {
      set({ selectedEngineerId: engineerId, whyOpen: get().whyOpen && (engineerId !== null || get().selectedRequestId !== null) });
    },

    openBrigade(engineerId) {
      set({ selectedRequestId: null, selectedEngineerId: engineerId });
    },

    toggleWhy() {
      const { whyOpen, selectedRequestId, selectedEngineerId } = get();
      set({ whyOpen: !whyOpen && (selectedRequestId !== null || selectedEngineerId !== null) });
    },

    closeWhy() {
      set({ whyOpen: false });
    },

    setTab(tabId) {
      set({ activeTab: tabId });
    },

    setShowPrevious(value) {
      set({ showPrevious: value && Boolean(get().state?.previous_plan) });
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
      resetAppSession();
      saveDatasetId(null);
      // Нагрузку и обед диспетчер выбирал сам: следующий файл он планирует с тем же выбором.
      const { config, workloadLevel, lunchEnabled } = get();
      set({ ...initialAppData, config, workloadLevel, lunchEnabled });
    },
  };
});
