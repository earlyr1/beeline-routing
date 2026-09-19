// Типы API строго по docs/superpowers/specs/2026-09-15-api-contract.md.
// Отличия в именах: Request из контракта здесь ServiceRequest, Event здесь PlanEvent,
// чтобы не затенять глобальные DOM-типы Request и Event.

export type Skill = 'local' | 'connection' | 'emergency';
export type Transport = 'car' | 'bike' | 'public';
export type Priority = 'normal' | 'urgent';
/** Приоритет распределения по роду работ (ответ организаторов, вопрос 15): авария → подключение → ремонт и дозаказ. */
export type RequestTier = 'emergency' | 'connection' | 'routine';
export type RequestStatus = 'active' | 'cancelled';
export type EventType =
  | 'urgent'
  | 'cancel'
  | 'restore'
  | 'engineer_unavailable'
  | 'engineer_transport_changed'
  | 'request_updated'
  | 'engineer_delayed'
  | 'request_reassigned';
export type ReasonCode =
  | 'no_skill'
  | 'no_transport'
  | 'does_not_fit_window_or_shift'
  | 'no_free_engineer_in_window'
  | 'address_not_found';
export type GeocodePrecision = 'house' | 'street' | 'locality' | 'none';
export type DatasetStatusValue = 'processing' | 'ready' | 'failed';
export type DatasetStage = 'parsing' | 'geocoding' | 'matrix' | 'solving' | 'ready';
export type ProposalStatus = 'pending' | 'approved' | 'rejected' | 'failed';
export type SolverName = 'ortools' | 'fcfs' | 'dispatchers';
export type MatrixSource = 'osrm' | 'haversine';
/** Время в формате HH:MM */
export type HHMM = string;

export interface ServiceRequest {
  id: string;
  address: string;
  lat: number | null;
  lon: number | null;
  geocode_precision: GeocodePrecision;
  district: string;
  duration_min: number;
  window_start: HHMM;
  window_end: HHMM;
  /**
   * Срочная заявка «как можно скорее»: окно задаёт сервер, от времени события до самого позднего конца смен.
   * Ожидание до 4 часов после window_start бесплатно, дольше небольшой штраф.
   */
  asap: boolean;
  priority: Priority;
  /**
   * Уровень распределения по роду работ: при нехватке ресурсов первым снимается ремонт и дозаказ,
   * затем подключение, последней — авария. Приходит из типа заявки BK; в старых данных нижний уровень.
   * Это не то же самое, что priority: «Срочная» отмечает состояние заявки, уровень — род работ.
   */
  tier: RequestTier;
  skill: Skill;
  transport_required: Transport | null;
  status: RequestStatus;
  source_type_bk: string;
  source_type_hd: string;
  /**
   * Инженеру нужно взять с собой единицу оборудования: роутер, приставку или колонку.
   * На заявку приходится ровно одна единица дневного запаса бригады (Engineer.equipment_stock).
   */
  needs_equipment: boolean;
  /**
   * Бригада, которую диспетчер выбрал для заявки вручную; null — заявку распределяет оптимизатор.
   * Выбор держится и после следующих событий, пока бригада может взять заявку.
   */
  fixed_engineer_id?: string | null;
}

export interface Engineer {
  id: string;
  name: string;
  start_lat: number;
  start_lon: number;
  shift_start: HHMM;
  shift_end: HHMM;
  skills: Skill[];
  transport: Transport;
  available: boolean;
  unavailable_from: HHMM | null;
  /**
   * Сколько единиц оборудования бригада получает в офисе утром сразу на весь день.
   * Каждая заявка с needs_equipment тратит одну; больше этого числа таких заявок бригаде не назначают.
   */
  equipment_stock: number;
}

export interface Office {
  region: string;
  title: string;
  address: string;
  lat: number;
  lon: number;
}

export interface PlanEvent {
  type: EventType;
  time: HHMM;
  request: ServiceRequest | null;
  request_id: string | null;
  engineer_id: string | null;
  /** Новый транспорт для engineer_transport_changed. */
  transport?: Transport | null;
  /** Транспорт до смены; заполняет сервер у применённого события, клиентское значение игнорируется. */
  previous_transport?: Transport | null;
  /** Заявка до изменения у request_updated; заполняет сервер у применённого события, клиентское значение игнорируется. */
  previous_request?: ServiceRequest | null;
  /** На сколько минут задерживается инженер у engineer_delayed, от 5 до 480. */
  delay_min?: number | null;
  /**
   * Бригада заявки до переназначения у request_reassigned; null — заявка была без бригады.
   * Заполняет сервер у применённого события, клиентское значение игнорируется.
   */
  previous_engineer_id?: string | null;
}

export interface Visit {
  request_id: string;
  arrival: HHMM;
  start: HHMM;
  end: HHMM;
  leg_km: number;
  leg_min: number;
  late_min: number;
  pinned: boolean;
}

/** Обед по плану: 45 минут между визитами, в окне от трёх до шести часов после начала смены. */
export interface RouteLunch {
  start: HHMM;
  end: HHMM;
}

export interface Route {
  engineer_id: string;
  visits: Visit[];
  total_km: number;
  total_travel_min: number;
  /** null: у инженера нет визитов, смена короче шести часов, окно обеда прошло без обеда или это план диспетчеров. */
  lunch: RouteLunch | null;
}

export interface Unassigned {
  request_id: string;
  reason_code: ReasonCode;
  reason_text: string;
}

export interface Metrics {
  engineers_used: number;
  km_per_engineer: Record<string, number>;
  total_km: number;
  assigned: number;
  unassigned: number;
  violations: number;
}

export interface Plan {
  solver: SolverName;
  routes: Route[];
  unassigned: Unassigned[];
  metrics: Metrics;
  violations: string[];
}

export interface UploadReport {
  region: string;
  region_title: string;
  source: 'beeline_csv' | 'bundle';
  requests: number;
  engineers: number;
  skipped_rows: string[];
  geocoding: Record<GeocodePrecision, number>;
  not_found: { request_id: string; address: string }[];
  matrix_source: MatrixSource;
}

export interface DatasetStatus {
  dataset_id: string;
  status: DatasetStatusValue;
  stage: DatasetStage;
  progress: { done: number; total: number };
  report: UploadReport | null;
  error: string | null;
}

export interface AppliedEvent {
  id: string;
  event: PlanEvent;
  version: number;
}

export interface ConstraintCheck {
  name: string;
  ok: boolean;
  detail: string;
}

export interface Alternative {
  engineer_id: string;
  feasible: boolean;
  extra_km: number | null;
  start: HHMM | null;
  reason: string;
}

export interface Explanation {
  request_id: string;
  status: 'assigned' | 'unassigned' | 'cancelled';
  engineer_id: string | null;
  summary: string;
  factors: string[];
  constraints: ConstraintCheck[];
  visit: Visit | null;
  alternatives: Alternative[];
  unassigned: Unassigned | null;
}

export interface DiffMove {
  request_id: string;
  from_engineer_id: string;
  to_engineer_id: string;
}

export interface DiffAssign {
  request_id: string;
  engineer_id: string;
}

export interface DiffRemove {
  request_id: string;
  engineer_id: string;
  reason: string;
}

export interface DiffShift {
  request_id: string;
  engineer_id: string;
  old_start: HHMM;
  new_start: HHMM;
  delta_min: number;
}

/** Визит, к которому инженер опоздал бы, если после задержки не перепланировать день. */
export interface LateVisitForecast {
  request_id: string;
  /** Начало в плане до задержки. */
  planned_start: HHMM;
  forecast_start: HHMM;
  /** Насколько прогнозное начало позже конца окна, минут. */
  late_min: number;
}

/** Прогноз последствий задержки без перепланирования: только у engineer_delayed. */
export interface DelayForecast {
  engineer_id: string;
  delay_min: number;
  late_without_replan: LateVisitForecast[];
  overtime_without_replan_min: number;
}

export interface PlanDiff {
  moved: DiffMove[];
  added: DiffAssign[];
  removed: DiffRemove[];
  reordered_engineers: string[];
  time_shifts: DiffShift[];
  metrics_before: Metrics;
  metrics_after: Metrics;
  /** Прогноз опозданий для задержки инженера; null у остальных событий. */
  delay_forecast?: DelayForecast | null;
}

export interface PlanningState {
  dataset_id: string;
  version: number;
  region: string;
  office: Office;
  now: HHMM;
  requests: ServiceRequest[];
  engineers: Engineer[];
  plan: Plan;
  previous_plan: Plan | null;
  baseline: Plan;
  control: Plan | null;
  last_diff: PlanDiff | null;
  events: AppliedEvent[];
  matrix_source: MatrixSource;
  /** Нагрузка инженеров сессии от 0 (спокойный день) до 2 (на пределе): её используют все расчёты дня. */
  workload_level: number;
  /** Обед по плану в сессии: без него маршруты без обеда, а план дня с нуля считается быстрее. */
  lunch_enabled: boolean;
  /**
   * Время на шкале дня, на которое показан план: применены все события шкалы не позже него.
   * now, events, last_diff и previous_plan относятся к плану на это время.
   */
  cursor: HHMM;
  /** События шкалы дня в порядке применения: по времени, при равном времени по порядку добавления. */
  timeline: TimelineItem[];
  /** Сервер уже посчитал план после каждого события шкалы; пока false, статусы событий впереди могут измениться. */
  timeline_ready: boolean;
  /** «Ломающее» событие, на котором стоит время, и его варианты; null — выбирать нечего. */
  pending_choice?: EventChoice | null;
}

/**
 * applied — применено к плану на cursor; pending — впереди, позже cursor; rejected — сервер его не принял;
 * awaiting — «ломающее» событие без выбора, на котором остановилось время.
 */
export type TimelineStatus = 'applied' | 'pending' | 'rejected' | 'awaiting';

/** Событие на шкале дня. */
export interface TimelineItem {
  /** Номер вида tl_<n>: не повторяется в наборе данных. */
  id: string;
  /** Применённое событие со значениями сервера (previous_*), иначе событие как его запланировали. */
  event: PlanEvent;
  status: TimelineStatus;
  /** Почему событие отклонено; null у применённых и событий впереди. */
  reason: string | null;
  /** Выбранная стратегия исправления; null у «неломающих» событий и у ещё не выбранных. */
  variant: EventVariant | null;
  /** «Ломающее» событие: сервер предлагает для него варианты исправления. */
  choosable: boolean;
}

/** Три стратегии, которые сервер считает для «ломающего» события заранее. */
export type BaseVariant = 'optimal' | 'stable' | 'keep';

/**
 * «Отдать заявку выбранной бригаде»: assign:<инженер>. Сервер считает такой план только по запросу диспетчера
 * и только у срочной заявки.
 */
export type AssignVariant = `assign:${string}`;

/** Стратегия исправления плана на «ломающее» событие. */
export type EventVariant = BaseVariant | AssignVariant;

/** Вариант исправления: итоги и отличия от варианта, с которым его сравнили. */
export interface VariantOption {
  variant: EventVariant;
  title: string;
  summary: string;
  metrics: Metrics;
  /** Визиты, которые начнутся позже конца окна. */
  late: number;
  /** Заявки, переехавшие к другой бригаде относительно плана до события. */
  moved: number;
  pros: string[];
  cons: string[];
  recommended: boolean;
  /** Бригада, у которой заявка события в плане варианта; null — заявка без бригады или событие не про одну заявку. */
  request_engineer_id: string | null;
  /** Вариант, с которым сравнивают плюсы и минусы; у «отдать бригаде» это всегда «Оптимально по дню». */
  compared_to: EventVariant | null;
}

/** Варианты исправления для «ломающего» события шкалы. */
export interface EventChoice {
  entry_id: string;
  event: PlanEvent;
  metrics_before: Metrics;
  late_before: number;
  variants: VariantOption[];
  current: EventVariant | null;
  /** Диспетчер может отдать заявку конкретной бригаде: только у срочной заявки. */
  assignable: boolean;
}

/** Тело POST /api/datasets/{id}/cursor. */
export interface CursorRequest {
  time: HHMM;
}

/** Тело POST /api/datasets/{id}/plan: пропущенное поле оставляет значение сессии. */
export interface PlanRequest {
  /** Нагрузка инженеров от 0 до 2. */
  workload_level?: number;
  /** Обед по плану у каждого инженера. */
  lunch?: boolean;
}

export interface RouteLeg {
  to_request_id: string;
  /** [lon, lat] */
  coordinates: [number, number][];
}

export interface RouteGeometry {
  engineer_id: string;
  /** Версия плана, по которому построена геометрия: кэш линий не путает планы на разное время. */
  version: number;
  transport: Transport;
  source: 'osrm' | 'straight';
  legs: RouteLeg[];
}

export interface Proposal {
  id: string;
  status: ProposalStatus;
  event: PlanEvent;
  rationale: string;
  source_text: string;
  created_at_version: number;
  result_diff: PlanDiff | null;
  error: string | null;
}

export interface ChatResponse {
  proposals: Proposal[];
  clarification: string | null;
}

export interface ApproveResponse {
  proposal: Proposal;
  state: PlanningState;
}

export interface ApproveAllResponse {
  proposals: Proposal[];
  state: PlanningState;
}

/** Адрес по точке на карте: ответ GET /api/geocode/reverse. */
export interface ReverseGeocode {
  /** Короткий адрес, который разбирает геокодер backend, например «Москва, Перовская улица, 42к1»; null — по точке ничего не известно. */
  address: string | null;
  precision: GeocodePrecision;
}

export interface ClientConfig {
  yandex_maps_api_key: string | null;
  llm_enabled: boolean;
  osrm_available: boolean;
}
