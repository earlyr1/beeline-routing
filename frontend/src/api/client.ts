import type {
  ApproveAllResponse,
  ApproveResponse,
  ChatResponse,
  ClientConfig,
  CursorRequest,
  DatasetStatus,
  EventChoice,
  EventVariant,
  Explanation,
  HHMM,
  PlanEvent,
  PlanningState,
  PlanRequest,
  Proposal,
  ReverseGeocode,
  RouteGeometry,
} from './types';

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function detailText(detail: unknown): string | null {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        typeof item === 'object' && item !== null && 'msg' in item ? String((item as { msg: unknown }).msg) : String(item),
      )
      .join('; ');
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError(0, 'Сервер недоступен. Проверьте, что backend запущен.');
  }
  if (!response.ok) {
    let message = `Ошибка сервера ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      message = detailText(body.detail) ?? message;
    } catch {
      // тело ответа не JSON
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

const postJson = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

const putJson = (body: unknown): RequestInit => ({ ...postJson(body), method: 'PUT' });

const dataset = (datasetId: string) => `/datasets/${encodeURIComponent(datasetId)}`;

export const getConfig = () => request<ClientConfig>('/config');

export const getHealth = () => request<{ status: string }>('/health');

export function uploadFile(file: File): Promise<DatasetStatus> {
  const form = new FormData();
  form.append('file', file);
  return request<DatasetStatus>('/upload', { method: 'POST', body: form });
}

export const getDatasetStatus = (datasetId: string) => request<DatasetStatus>(dataset(datasetId));

/** Построить план дня; пропущенные нагрузку и обед сервер берёт из сессии. */
export function buildPlan(datasetId: string, body: PlanRequest = {}): Promise<PlanningState> {
  const given = Object.fromEntries(Object.entries(body).filter(([, value]) => value !== undefined));
  return request<PlanningState>(
    `${dataset(datasetId)}/plan`,
    Object.keys(given).length === 0 ? { method: 'POST' } : postJson(given),
  );
}

export const getPlanningState = (datasetId: string) => request<PlanningState>(`${dataset(datasetId)}/state`);

/** Прежний способ: событие применяется, и план сразу переходит на его время. Экран пользуется шкалой дня. */
export const postEvent = (datasetId: string, event: PlanEvent) =>
  request<PlanningState>(`${dataset(datasetId)}/events`, postJson(event));

/**
 * Событие на шкалу дня: время плана не меняется, событие позже него остаётся впереди.
 * variant — стратегия события сразу, без окна выбора: так отменяется заявка отказавшегося клиента.
 */
export const addTimelineEvent = (datasetId: string, event: PlanEvent, variant?: EventVariant) =>
  request<PlanningState>(
    `${dataset(datasetId)}/timeline/events${variant ? `?${new URLSearchParams({ variant })}` : ''}`,
    postJson(event),
  );

export const deleteTimelineEvent = (datasetId: string, entryId: string) =>
  request<PlanningState>(`${dataset(datasetId)}/timeline/events/${encodeURIComponent(entryId)}`, { method: 'DELETE' });

/**
 * Варианты исправления для «ломающего» события шкалы. С assign сервер считает ещё один план — с заявкой
 * у выбранной бригады — и присылает его четвёртым вариантом.
 */
export const getTimelineVariants = (datasetId: string, entryId: string, assign?: string) =>
  request<EventChoice>(
    `${dataset(datasetId)}/timeline/events/${encodeURIComponent(entryId)}/variants${assign ? `?${new URLSearchParams({ assign })}` : ''}`,
  );

/** Выбрать или поменять стратегию события: план пересчитывается с этого события. */
export const setTimelineVariant = (datasetId: string, entryId: string, variant: EventVariant) =>
  request<PlanningState>(`${dataset(datasetId)}/timeline/events/${encodeURIComponent(entryId)}/variant`, putJson({ variant }));

/** Сброс событий: шкала пустеет, план — утренний план дня без пересчёта. */
export const clearTimeline = (datasetId: string) =>
  request<PlanningState>(`${dataset(datasetId)}/timeline`, { method: 'DELETE' });

/** План на время дня: применены все события шкалы не позже этого времени. */
export const moveCursor = (datasetId: string, time: HHMM) =>
  request<PlanningState>(`${dataset(datasetId)}/cursor`, postJson({ time } satisfies CursorRequest));

export const getExplanation = (datasetId: string, requestId: string) =>
  request<Explanation>(`${dataset(datasetId)}/explain/${encodeURIComponent(requestId)}`);

export const getRouteGeometry = (datasetId: string, engineerId: string) =>
  request<RouteGeometry>(`${dataset(datasetId)}/routes/${encodeURIComponent(engineerId)}/geometry`);

/** Адрес по точке на карте: подставляется в срочную заявку, добавленную кликом по карте. */
export const getReverseGeocode = (lat: number, lon: number) =>
  request<ReverseGeocode>(`/geocode/reverse?${new URLSearchParams({ lat: String(lat), lon: String(lon) })}`);

const proposal = (datasetId: string, proposalId: string) =>
  `${dataset(datasetId)}/proposals/${encodeURIComponent(proposalId)}`;

export const sendChat = (datasetId: string, text: string) =>
  request<ChatResponse>(`${dataset(datasetId)}/chat`, postJson({ text }));

export const getProposals = (datasetId: string) => request<Proposal[]>(`${dataset(datasetId)}/proposals`);

export const approveProposal = (datasetId: string, proposalId: string) =>
  request<ApproveResponse>(`${proposal(datasetId, proposalId)}/approve`, { method: 'POST' });

export const rejectProposal = (datasetId: string, proposalId: string) =>
  request<Proposal>(`${proposal(datasetId, proposalId)}/reject`, { method: 'POST' });

export const approveAllProposals = (datasetId: string) =>
  request<ApproveAllResponse>(`${dataset(datasetId)}/proposals/approve-all`, { method: 'POST' });

export const rejectAllProposals = (datasetId: string) =>
  request<Proposal[]>(`${dataset(datasetId)}/proposals/reject-all`, { method: 'POST' });
