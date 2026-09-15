// Типы API строго по docs/superpowers/specs/2026-09-15-api-contract.md.
// Отличия в именах: Request из контракта здесь ServiceRequest, Event здесь PlanEvent,
// чтобы не затенять глобальные DOM-типы Request и Event.

export type Skill = 'local' | 'connection' | 'emergency';
export type Transport = 'car' | 'foot' | 'bike' | 'public';
export type Priority = 'normal' | 'urgent';
export type RequestStatus = 'active' | 'cancelled';
export type EventType = 'urgent' | 'cancel' | 'restore' | 'engineer_unavailable';
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
  priority: Priority;
  skill: Skill;
  transport_required: Transport | null;
  status: RequestStatus;
  source_type_bk: string;
  source_type_hd: string;
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

export interface Route {
  engineer_id: string;
  visits: Visit[];
  total_km: number;
  total_travel_min: number;
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

export interface PlanDiff {
  moved: DiffMove[];
  added: DiffAssign[];
  removed: DiffRemove[];
  reordered_engineers: string[];
  time_shifts: DiffShift[];
  metrics_before: Metrics;
  metrics_after: Metrics;
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
}

export interface RouteLeg {
  to_request_id: string;
  /** [lon, lat] */
  coordinates: [number, number][];
}

export interface RouteGeometry {
  engineer_id: string;
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

export interface ClientConfig {
  yandex_maps_api_key: string | null;
  llm_enabled: boolean;
  osrm_available: boolean;
}
