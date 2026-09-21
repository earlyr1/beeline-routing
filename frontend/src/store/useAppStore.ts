import { create } from 'zustand';
import {
  addTimelineEvent,
  ApiError,
  buildPlan,
  clearTimeline,
  deleteTimelineEvent as removeTimelineEvent,
  getConfig,
  getDatasetStatus,
  getPlanningState,
  getReverseGeocode,
  getTimelineVariants,
  moveCursor,
  setTimelineVariant,
  uploadFile,
} from '../api/client';
import type { ClientConfig, DatasetStatus, EventChoice, EventVariant, HHMM, PlanEvent, PlanningState, TimelineItem } from '../api/types';
import { agreedWindow, type AgreedWindows } from '../lib/communications';
import type { PickedPoint } from '../lib/events';
import { fromMinutes, isValidTime, toMinutes } from '../lib/format';
import { byId } from '../lib/planView';
import { NO_TIMELINE_MOVE, newlyRejected, pausesAt, playEnd, rejectedNotice, timelineMove, type TimelineMove } from '../lib/timeBar';
import { dayScale } from '../lib/timeline';
import { CHOOSABLE_EVENTS } from '../lib/variants';
import { DEFAULT_LUNCH_ENABLED, DEFAULT_WORKLOAD_LEVEL, clampWorkloadLevel, lunchEnabledOf } from '../lib/workload';

export const POLL_INTERVAL_MS = 1000;
/** Сколько раз повторить опрос статуса после сбоя сети или ответа 5xx, прежде чем сдаться. */
export const POLL_RETRIES = 3;
/** Ключ localStorage с набором данных открытого плана: план переживает перезагрузку страницы и закрытие браузера. */
export const SESSION_DATASET_KEY = 'routing.datasetId';
/**
 * Ключ localStorage с согласованными окнами набора данных: отметки звонков переживают перезагрузку страницы.
 * Версия в ключе — из-за смены содержимого отметки: раньше в ней лежали время визита и бригада, теперь окно
 * клиента. Отметки прежней версии лежат под своим ключом и просто не читаются.
 */
export const agreedKey = (datasetId: string) => `routing.agreed.v2.${datasetId}`;
/** Шаг проигрывания дня: минута плана за 100 мс, то есть час дня за 6 секунд. */
export const PLAY_TICK_MS = 100;
/** Часы до первого ответа сервера: плана ещё нет, и шкалы дня, на начало которой их поставить, тоже. */
export const DAY_START: HHMM = '00:00';

/** Диалог, для которого диспетчер указывает точку на карте. */
export type PickOwner = 'urgent' | 'edit';

/** Диалог панели событий: на панели осталась только срочная заявка. */
export type ToolbarDialog = 'urgent';

/** Диалог инженера, который открывает страница бригады. */
export type EngineerDialogKind = 'transport' | 'unavailable';

export interface EngineerDialog {
  kind: EngineerDialogKind;
  /** Инженер со страницы бригады. */
  engineerId: string;
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
  /** Фильтр «Без исполнителя» во вкладке «Заявки»; он действует и под открытой страницей бригады. */
  unassignedOnly: boolean;
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
  /** Окно выбора варианта исправления; null — закрыто или ждёт варианты. */
  choice: EventChoice | null;
  /** Окно выбора открыто и ждёт варианты от сервера. */
  choiceLoading: boolean;
  /** Карточка «отдать бригаде» ждёт план с выбранной бригадой; остальные карточки окна остаются доступными. */
  assignLoading: boolean;
  /** Куда вернуть часы после выбора: время и шли ли часы до остановки на событии. */
  resumeAfterChoice: { time: HHMM; play: boolean } | null;
  /** Событие, окно которого закрыли без выбора: ответы сервера его не открывают, пока часы не пойдут дальше. */
  dismissedChoice: string | null;
  busy: boolean;
  error: string | null;
  pickMode: boolean;
  pickedPoint: PickedPoint | null;
  /** Чья точка на карте: точку видит только диалог, который начал выбор. */
  pickFor: PickOwner | null;
  /** Заявка, открытая в диалоге «Изменить заявку»; null, когда диалог закрыт. */
  editingRequestId: string | null;
  delayDialogOpen: boolean;
  /** Инженер со страницы бригады для диалога «Задержка инженера»; null — диалог закрыт. */
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
  /**
   * Что уже согласовано с клиентом: номер заявки → окно, которое ему назвали (null — сказали, что сегодня
   * не приедем). С ним вкладка «Коммуникации» сравнивает план; отметка снимается сама, когда окно снова уедет.
   */
  agreed: AgreedWindows;
}

export interface AppActions {
  loadConfig(): Promise<void>;
  upload(file: File): Promise<void>;
  plan(): Promise<void>;
  /**
   * Поставить событие на шкалу дня. Сначала сервер переводит план на время часов, чтобы событие у часов применилось сразу.
   * variant — стратегия события сразу, без окна выбора: так отменяется заявка отказавшегося клиента.
   */
  applyEvent(event: PlanEvent, variant?: EventVariant): Promise<boolean>;
  /** Сброс событий: все события шкалы убираются, план — утренний, часы на начале дня. */
  resetEvents(): Promise<boolean>;
  /** Убрать событие со шкалы дня. */
  deleteTimelineEvent(entryId: string): Promise<boolean>;
  /** Выбрать стратегию для открытого окна; часы возвращаются туда, откуда их остановило событие. */
  chooseVariant(variant: EventVariant): Promise<boolean>;
  /** Открыть окно выбора для события шкалы (смена выбора с метки). */
  openChoice(entryId: string): Promise<void>;
  /** Посчитать для открытого окна ещё один вариант: заявку берёт выбранная бригада. */
  previewAssign(engineerId: string): Promise<void>;
  /** Закрыть окно без выбора: часы стоят на событии. */
  closeChoice(): void;
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
  setUnassignedOnly(value: boolean): void;
  /** Отметить, что клиенту назвали окно заявки из текущего плана (или сказали, что сегодня не приедем). */
  markAgreed(requestId: string): void;
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
  restoring: false,
  state: null,
  selectedRequestId: null,
  selectedEngineerId: null,
  whyOpen: false,
  activeTab: 'requests',
  unassignedOnly: false,
  clock: DAY_START,
  dragging: false,
  playing: false,
  committing: false,
  timelineMove: NO_TIMELINE_MOVE,
  choice: null,
  choiceLoading: false,
  assignLoading: false,
  resumeAfterChoice: null,
  dismissedChoice: null,
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
  agreed: {},
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

/** Окно выбора варианта закрыто и ничего не ждёт. */
const NO_CHOICE = {
  choice: null,
  choiceLoading: false,
  assignLoading: false,
  resumeAfterChoice: null,
  dismissedChoice: null,
} satisfies Partial<AppData>;

const OFFLINE_CONFIG: ClientConfig = { yandex_maps_api_key: null, llm_enabled: false, osrm_available: false };

function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return 'Неизвестная ошибка';
}

const withPeriod = (text: string) => (/[.!?…]$/u.test(text) ? text : `${text}.`);

/** Более позднее из двух времён; неправильное время уступает второму. */
const laterOf = (a: HHMM, b: HHMM): HHMM => (isValidTime(a) && isValidTime(b) && toMinutes(a) > toMinutes(b) ? a : b);

const isTransient = (error: unknown) => error instanceof ApiError && (error.status === 0 || error.status >= 500);

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Прежний план пропал: backend перезапускали, а наборы данных живут в его памяти. */
export const LOST_SESSION_MESSAGE = 'Прежний план недоступен: сервис перезапускался. Загрузите файл заново.';

function savedDatasetId(): string | null {
  try {
    // Раньше план помнила только вкладка (sessionStorage): открытая до обновления вкладка продолжает свой план.
    return localStorage.getItem(SESSION_DATASET_KEY) ?? sessionStorage.getItem(SESSION_DATASET_KEY);
  } catch {
    return null;
  }
}

function loadAgreed(datasetId: string): AgreedWindows {
  try {
    const saved = localStorage.getItem(agreedKey(datasetId));
    return saved ? (JSON.parse(saved) as AgreedWindows) : {};
  } catch {
    // Хранилище недоступно или в нём мусор: считаем, что клиентам ещё не звонили.
    return {};
  }
}

function saveAgreed(datasetId: string, agreed: AgreedWindows): void {
  try {
    localStorage.setItem(agreedKey(datasetId), JSON.stringify(agreed));
  } catch {
    // Хранилище недоступно (приватный режим или запрет браузера): отметки живут до перезагрузки страницы.
  }
}

function saveDatasetId(datasetId: string | null): void {
  try {
    sessionStorage.removeItem(SESSION_DATASET_KEY);
    if (datasetId === null) localStorage.removeItem(SESSION_DATASET_KEY);
    else localStorage.setItem(SESSION_DATASET_KEY, datasetId);
  } catch {
    // Хранилище недоступно (приватный режим или запрет браузера): просто не запоминаем план.
  }
}

/** Сообщение об отклонённых событиях шкалы для всплывающей ошибки. */
function rejectedMessage(items: TimelineItem[], state: PlanningState): string {
  const engineers = byId(state.engineers);
  const requests = byId(state.requests);
  return items.map((item) => rejectedNotice(item, engineers, requests)).join(' ');
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
    const { state: shown, selectedRequestId, selectedEngineerId, editingRequestId, whyOpen, clock, playing: wasPlaying } = get();
    const same = shown !== null && shown.dataset_id === next.dataset_id ? shown : null;
    const exists = (requestId: string | null) => requestId !== null && next.requests.some((request) => request.id === requestId);
    const rejected = same ? newlyRejected(same.timeline ?? [], next.timeline ?? []) : [];
    saveDatasetId(next.dataset_id);
    set({
      state: next,
      datasetId: next.dataset_id,
      clock: syncClock && isValidTime(next.cursor ?? '') ? next.cursor : clock,
      timelineMove: same ? timelineMove(same, next) : NO_TIMELINE_MOVE,
      selectedRequestId: exists(selectedRequestId) ? selectedRequestId : null,
      // Заявка пропала из плана, а бригада не выбрана: объяснять в панели «Почему» больше нечего.
      whyOpen: whyOpen && (exists(selectedRequestId) || selectedEngineerId !== null),
      editingRequestId: exists(editingRequestId) ? editingRequestId : null,
      // Нагрузка и обед сессии на сервере: «Применить» и восстановленный план продолжают с ними.
      workloadLevel: clampWorkloadLevel(next.workload_level),
      lunchEnabled: lunchEnabledOf(next.lunch_enabled),
      // Открыли другой набор данных (или этот же после перезагрузки страницы): берём его отметки звонков.
      ...(same ? {} : { agreed: loadAgreed(next.dataset_id) }),
      ...(rejected.length > 0 ? { error: rejectedMessage(rejected, next) } : {}),
    });
    // Время плана остановилось на «ломающем» событии без выбора: часы ждут на нём, открывается окно выбора.
    const pending = next.pending_choice ?? null;
    // Закрытое окно помнится, только пока план стоит на том же событии: часы ушли назад — дойдя до него, снова спросят.
    if (get().dismissedChoice !== (pending?.entry_id ?? null)) set({ dismissedChoice: null });
    const { choice, dismissedChoice } = get();
    if (pending && pending.entry_id !== choice?.entry_id && pending.entry_id !== dismissedChoice) {
      stopTicking();
      set({
        playing: false,
        clock: next.cursor,
        choice: pending,
        choiceLoading: false,
        assignLoading: false,
        resumeAfterChoice: { time: laterOf(clock, next.cursor), play: wasPlaying },
      });
    }
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
      // Берём только статусы шкалы и готовность: план и выбор на экране не сбрасываются.
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
    const end = playEnd(dayScale(state));
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
        whyOpen: false,
        ...NO_FLOATING_DIALOG,
        ...NO_CLOCK_ACTIVITY,
        toolbarDialog: null,
        mapMenu: null,
        ...NO_ADDRESS_LOOKUP,
        ...NO_CHOICE,
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
      set({ busy: true, error: null, ...NO_CHOICE });
      try {
        const state = await enqueue(current, () => buildPlan(datasetId, { workload_level: workloadLevel, lunch: lunchEnabled }));
        if (!isCurrent(current)) return;
        get().setPlanningState(state);
        // Пересчитанный день начинается заново, как после сброса событий: обзвона в нём ещё не было.
        saveAgreed(datasetId, {});
        // Новый день начинается со своего начала: часы встают на начало шкалы дня, и это время уходит на сервер,
        // чтобы курсор плана совпал с часами. Шкала дня после пересчёта пуста, и перевод курсора ничего не считает.
        // Карточка заявки закрывается; панель «Почему» остаётся, только если открыта бригада.
        set({
          agreed: {},
          selectedRequestId: null,
          whyOpen: get().whyOpen && get().selectedEngineerId !== null,
          clock: fromMinutes(dayScale(state).from),
        });
        await get().commitClock();
      } catch (error) {
        if (isCurrent(current) && !(error instanceof StaleSession)) set({ error: errorMessage(error) });
      } finally {
        if (isCurrent(current)) set({ busy: false });
      }
    },

    async applyEvent(event, variant) {
      const { datasetId, clock } = get();
      if (!datasetId) return false;
      const current = generation;
      // «Ломающее» событие на время часов или раньше: сервер сразу попросит выбрать вариант, окно ждёт его ответа.
      const asks = CHOOSABLE_EVENTS.has(event.type) && isValidTime(event.time) && isValidTime(clock) && toMinutes(event.time) <= toMinutes(clock);
      set({ busy: true, error: null, ...(asks ? { choice: null, choiceLoading: true, assignLoading: false, dismissedChoice: null } : {}) });
      try {
        // Диспетчер видит на часах их время и ждёт, что событие в это время применится сразу.
        await get().commitClock();
        const state = await enqueue(current, () => addTimelineEvent(datasetId, event, variant));
        if (!isCurrent(current)) return false;
        // Окно «Считаем варианты…» закрыли до ответа: как закрытое без выбора, оно откроется на «Запустить» или сдвиге вперёд.
        const { choice, choiceLoading } = get();
        if (asks && !choiceLoading && choice === null) set({ dismissedChoice: state.pending_choice?.entry_id ?? null });
        get().setPlanningState(state);
        return true;
      } catch (error) {
        if (isCurrent(current) && !(error instanceof StaleSession)) set({ error: errorMessage(error) });
        return false;
      } finally {
        if (isCurrent(current)) set({ busy: false });
        // Сервер не остановил время на событии (или отклонил его): окно, ждавшее варианты, закрывается.
        if (isCurrent(current) && get().choiceLoading) set({ choiceLoading: false });
      }
    },

    async resetEvents() {
      const { datasetId } = get();
      if (!datasetId) return false;
      // Сброс ставит часы на начало дня: идущие часы останавливаются без фиксации, окно выбора закрывается.
      get().stopPlayback();
      const current = generation;
      set({ busy: true, error: null, ...NO_CHOICE });
      try {
        const state = await enqueue(current, () => clearTimeline(datasetId));
        if (!isCurrent(current)) return false;
        get().setPlanningState(state);
        // День начинается заново: обзвона ещё не было, отметки «Согласовано» снимаются вместе с событиями.
        saveAgreed(datasetId, {});
        set({ agreed: {}, clock: fromMinutes(dayScale(state).from) });
        await get().commitClock();
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

    async chooseVariant(variant) {
      const { datasetId, choice, resumeAfterChoice } = get();
      if (!datasetId || !choice) return false;
      const current = generation;
      set({ busy: true, error: null });
      let chosen = false;
      try {
        const state = await enqueue(current, () => setTimelineVariant(datasetId, choice.entry_id, variant));
        if (!isCurrent(current)) return false;
        set(NO_CHOICE);
        get().setPlanningState(state);
        chosen = true;
      } catch (error) {
        if (isCurrent(current) && !(error instanceof StaleSession)) set({ error: errorMessage(error) });
      } finally {
        if (isCurrent(current)) set({ busy: false });
      }
      if (!chosen || !isCurrent(current)) return chosen;
      // Ответ уже остановился на следующем событии и открыл его окно: часы вернутся туда же, но после этого выбора.
      const opened = get().resumeAfterChoice;
      if (get().choice !== null) {
        if (resumeAfterChoice && opened) set({ resumeAfterChoice: { time: laterOf(resumeAfterChoice.time, opened.time), play: resumeAfterChoice.play } });
        return true;
      }
      if (!resumeAfterChoice) return true;
      // Часы шли до события — идут дальше; ползунок отпустили за событием — план догоняет отпущенное время.
      if (resumeAfterChoice.play) {
        get().play();
      } else if (resumeAfterChoice.time !== get().clock) {
        set({ clock: resumeAfterChoice.time });
        await get().commitClock();
      }
      return true;
    },

    async openChoice(entryId) {
      const { datasetId } = get();
      if (!datasetId) return;
      const current = generation;
      set({ choice: null, choiceLoading: true, assignLoading: false, resumeAfterChoice: null, error: null });
      try {
        const choice = await getTimelineVariants(datasetId, entryId);
        // Окно закрыли, пока варианты считались: ответ его заново не открывает.
        if (isCurrent(current) && get().choiceLoading) set({ choice, choiceLoading: false });
      } catch (error) {
        if (isCurrent(current)) set({ choiceLoading: false, error: errorMessage(error) });
      }
    },

    async previewAssign(engineerId) {
      const { datasetId, choice } = get();
      if (!datasetId || !choice) return;
      const current = generation;
      set({ assignLoading: true, error: null });
      try {
        const next = await getTimelineVariants(datasetId, choice.entry_id, engineerId);
        // Окно успели закрыть или оно уже про другое событие: посчитанный вариант его не возвращает.
        if (isCurrent(current) && get().choice?.entry_id === choice.entry_id) set({ choice: next });
      } catch (error) {
        if (isCurrent(current)) set({ error: errorMessage(error) });
      } finally {
        if (isCurrent(current)) set({ assignLoading: false });
      }
    },

    closeChoice() {
      set({ dismissedChoice: get().choice?.entry_id ?? null, choice: null, choiceLoading: false, assignLoading: false, resumeAfterChoice: null });
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
      // Часы пытаются пройти событие, окно которого закрыли без выбора: сервер снова остановит их, окно откроется.
      if (state.pending_choice && isValidTime(clock) && toMinutes(clock) > toMinutes(state.cursor)) set({ dismissedChoice: null });
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
      // Время стоит на событии без выбора: вместо запуска снова открывается окно, после выбора часы пойдут.
      if (state.pending_choice) {
        set({ dismissedChoice: null, choice: state.pending_choice, resumeAfterChoice: { time: state.cursor, play: true } });
        return;
      }
      const scale = dayScale(state);
      const end = playEnd(scale);
      // Часы раньше начала шкалы стоят на ползунке в её начале: проигрывание идёт с того места, где ползунок.
      const start = Math.max(isValidTime(clock) ? toMinutes(clock) : scale.from, scale.from);
      if (start >= end) return;
      set({ playing: true, clock: fromMinutes(start) });
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
      set({ dragging: true, playing: false });
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

    setUnassignedOnly(value) {
      set({ unassignedOnly: value });
    },

    markAgreed(requestId) {
      const { state, datasetId, agreed, config } = get();
      if (!state || !datasetId) return;
      // Запоминаем окно, которое клиент теперь знает (в окно заявки план не попал — то, которое назвали вместо него):
      // когда обещание снова разойдётся с планом, отметка сама перестанет совпадать. Названное окно — слот сетки.
      const next = { ...agreed, [requestId]: agreedWindow(state, requestId, config?.window_grid ?? []) };
      saveAgreed(datasetId, next);
      set({ agreed: next });
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
