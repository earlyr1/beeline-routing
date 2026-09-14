# План 3: Frontend диспетчера — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Веб-интерфейс диспетчера: загрузка CSV/JSON с прогрессом предподсчёта, карта Яндекса со стартами инженеров и маршрутами, список заявок с отменой и возвратом, таймлайн, неназначенные с причинами, сравнение с базовым вариантом и диспетчерами, объяснение по заявке, срочная заявка и недоступность инженера, баннер изменений с переключателем «до / после».

**Architecture:** Одностраничное приложение на Vite, React 18 и TypeScript. Всё состояние живёт в одном zustand-сторе `useAppStore`, который ходит в backend только через `src/api/client.ts` строго по API-контракту. Всё, что считается для отображения (какой план показать, индексы назначений, пометки diff, шкала таймлайна, строки сравнения, сборка событий), вынесено в чистые функции `src/lib/*` с unit-тестами. Яндекс Карты грузятся динамически по ключу из `GET /api/config`; компонент карты получает реализацию через проп `components`, поэтому в тестах подставляются фейковые компоненты. В проде nginx раздаёт сборку и проксирует `/api` в `backend:8001`.

**Tech Stack:** Node 22.12+ (проверено на Node 26 и в образе node:22-alpine), React 18.3.1, TypeScript 5.9.3, Vite 7.3.6, zustand 5.0.15, Vitest 3.2.7, jsdom 26.1.0, Testing Library (react 16.3.3, jest-dom 6.10.0), Yandex Maps JS API v3 с типами `@yandex/ymaps3-types`, nginx 1.27.

**Spec:** `docs/superpowers/specs/2026-09-15-field-service-routing-design.md` (разделы 1, 2, 10, 13), API-контракт `docs/superpowers/specs/2026-09-15-api-contract.md`, конспект ТЗ `research/tz-and-data-notes.md`. Исполнитель читает контракт перед Задачей 1.

## Global Constraints

- Backend-интерфейс берётся только из API-контракта. Все пути начинаются с `/api`. В dev Vite проксирует `/api` на `http://localhost:8001`; в docker nginx проксирует на `http://backend:8001`, наружу compose отдаёт порт 8000. Файл compose принадлежит Плану 2.
- Раскладка из контракта обязательна: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/store/useAppStore.ts`, `frontend/src/components/panel/tabs.ts` с массивом `{id, title, component}`, `frontend/src/lib/colors.ts` с функцией `engineerColor(engineerId, engineerIds)`. План 4 добавит вкладку `proposals` одной строкой в `PANEL_TABS`.
- В `types.ts` контрактный `Request` называется `ServiceRequest`, контрактный `Event` называется `PlanEvent`, чтобы не затенять глобальные DOM-типы `Request` и `Event`. Все поля один в один с контрактом.
- Весь текст интерфейса на русском. Названия навыков и транспорта дословно из ТЗ: «Локальные работы», «Работы на подключение и дозаказы», «Аварийные работы»; «Автомобиль», «Пешеход», «Велосипед», «Общественный транспорт».
- Время приходит строками `HH:MM`, часы не ограничены 24: в плане диспетчеров бывают `25:53` и `49:13`. Разбор только через `toMinutes` из `src/lib/format.ts`, без `Date`.
- Время события по умолчанию равно `state.now` и не может быть раньше него. Формы используют `noValidate`, чтобы вместо нативной подсказки браузера показывались русские ошибки.
- Инженер стартует из своей точки `start_lat`/`start_lon` (медоид адресов его бригады), а не из офиса. Маршрут рисуется от старта инженера, у каждого инженера свой стартовый маркер, офис показывается нейтральным второстепенным маркером.
- В реальных бандлах у всех смена 10:00–22:00. Ось таймлайна по умолчанию 08:00–23:00, расширяется под данные, но не выходит за 00:00–24:00; визиты за шкалой обрезаются, помечаются и сохраняют сырые значения в подсказке.
- Одна библиотека карт: Yandex Maps JS API v3. Без ключа показывается заглушка, всё остальное работает.
- Версии пакетов зафиксированы точно, как в `package.json` Задачи 1. Любое обновление версий отдельным решением.
- Команды npm выполняются из каталога `frontend/`, git из корня репозитория.
- Каждое сообщение коммита заканчивается строками:

  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ
  ```

---

## Карта файлов

| Файл | Ответственность |
|---|---|
| `frontend/package.json`, `tsconfig.json`, `vite.config.ts`, `index.html`, `.gitignore` | Проект, строгий TypeScript, прокси `/api`, настройки Vitest |
| `frontend/src/api/types.ts` | Все типы API-контракта |
| `frontend/src/api/client.ts` | Одна функция на эндпоинт, `ApiError` с русским текстом из `detail` |
| `frontend/src/lib/format.ts` | Подписи на русском, разбор и форматирование времени, км, дельт, адресов |
| `frontend/src/lib/colors.ts` | Палитра инженеров, `engineerColor` |
| `frontend/src/lib/planView.ts` | Какой план показать, индексы назначений, пометки diff, порядок списка, прямые отрезки маршрута |
| `frontend/src/lib/events.ts` | Валидация формы срочной заявки, сборка всех типов событий, описание события |
| `frontend/src/lib/timeline.ts` | Шкала и строки таймлайна |
| `frontend/src/lib/comparison.ts` | Строки таблицы сравнения трёх планов |
| `frontend/src/store/useAppStore.ts` | Единый стор: загрузка с опросом, план, события, выбор, вкладки, режим выбора точки |
| `frontend/src/test/setup.ts`, `fixtures.ts`, `store.ts` | Настройка jsdom, контрактная фикстура состояния, сброс стора |
| `frontend/src/components/UploadScreen.tsx` | Экран загрузки, прогресс, отчёт, кнопка «Спланировать» |
| `frontend/src/components/ExplanationCard.tsx` | Карточка объяснения по выбранной заявке |
| `frontend/src/components/panel/*` | Правая панель: реестр вкладок, заявки, таймлайн, неназначенные, сравнение |
| `frontend/src/components/events/*` | Панель событий, диалоги срочной заявки и недоступности инженера |
| `frontend/src/components/DiffBanner.tsx` | Итог последнего события и переключатель «до / после» |
| `frontend/src/components/map/*` | Загрузчик Яндекс Карт, геометрия маршрутов, содержимое карты, заглушки |
| `frontend/src/components/MetricsStrip.tsx`, `ErrorToast.tsx`, `MainScreen.tsx`, `src/App.tsx`, `src/main.tsx`, `src/styles.css` | Каркас основного экрана и стили |
| `frontend/Dockerfile`, `nginx.conf`, `.dockerignore` | Сборка и раздача в docker |

---

## Task 1: Каркас проекта, типы API и HTTP-клиент

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html`, `frontend/.gitignore`, `frontend/src/test/setup.ts`
- Create: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: API-контракт `docs/superpowers/specs/2026-09-15-api-contract.md`.
- Produces: все типы из `src/api/types.ts` (`ServiceRequest`, `Engineer`, `Office`, `PlanEvent`, `Visit`, `Route`, `Unassigned`, `Metrics`, `Plan`, `UploadReport`, `DatasetStatus`, `AppliedEvent`, `ConstraintCheck`, `Alternative`, `Explanation`, `DiffMove`, `DiffAssign`, `DiffRemove`, `DiffShift`, `PlanDiff`, `PlanningState`, `RouteLeg`, `RouteGeometry`, `Proposal`, `ClientConfig` и перечисления). Из `src/api/client.ts`: `class ApiError extends Error { status: number }`, `getConfig(): Promise<ClientConfig>`, `getHealth(): Promise<{status: string}>`, `uploadFile(file: File): Promise<DatasetStatus>`, `getDatasetStatus(datasetId: string): Promise<DatasetStatus>`, `buildPlan(datasetId: string): Promise<PlanningState>`, `getPlanningState(datasetId: string): Promise<PlanningState>`, `postEvent(datasetId: string, event: PlanEvent): Promise<PlanningState>`, `getExplanation(datasetId: string, requestId: string): Promise<Explanation>`, `getRouteGeometry(datasetId: string, engineerId: string, plan?: 'current' | 'previous'): Promise<RouteGeometry>`.

- [ ] **Step 1: Создать `frontend/package.json`**

```json
{
  "name": "routing-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "zustand": "5.0.15"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "6.10.0",
    "@testing-library/react": "16.3.3",
    "@testing-library/user-event": "14.6.7",
    "@types/react": "18.3.31",
    "@types/react-dom": "18.3.7",
    "@vitejs/plugin-react": "5.2.0",
    "@yandex/ymaps3-types": "1.0.21108841",
    "jsdom": "26.1.0",
    "typescript": "5.9.3",
    "vite": "7.3.6",
    "vitest": "3.2.7"
  }
}
```

- [ ] **Step 2: Создать конфигурацию TypeScript, Vite и точку входа HTML**

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "resolveJsonModule": true,
    "noEmit": true,
    "types": ["vite/client", "@yandex/ymaps3-types"]
  },
  "include": ["src", "vite.config.ts"]
}
```

`frontend/vite.config.ts`:

```ts
/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: { target: 'es2022' },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8001', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
```

`frontend/index.html`:

```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Маршруты выездных инженеров</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/.gitignore`:

```gitignore
node_modules
dist
coverage
```

`frontend/src/test/setup.ts`:

```ts
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => {
  cleanup();
});
```

- [ ] **Step 3: Установить зависимости**

Run: `cd frontend && npm install`
Expected: строка вида `added 164 packages`, создан `package-lock.json`. На npm 11 возможно предупреждение `esbuild ... install scripts not yet covered by allowScripts`: оно безвредно, сборка работает без этого скрипта.

- [ ] **Step 4: Создать `frontend/src/api/types.ts`**

```ts
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

export interface ClientConfig {
  yandex_maps_api_key: string | null;
  llm_enabled: boolean;
  osrm_available: boolean;
}
```

- [ ] **Step 5: Написать падающий тест клиента `frontend/src/api/client.test.ts`**

```ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, buildPlan, getRouteGeometry, postEvent, uploadFile } from './client';
import type { PlanEvent } from './types';

const cancel: PlanEvent = { type: 'cancel', time: '13:00', request: null, request_id: '50104', engineer_id: null };

const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as unknown as Response;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api client', () => {
  it('uploads the file as multipart form data', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(202, { dataset_id: 'd1', status: 'processing' }));
    vi.stubGlobal('fetch', fetchMock);
    const result = await uploadFile(new File(['a;b'], 'east.csv', { type: 'text/csv' }));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(result.dataset_id).toBe('d1');
    expect(url).toBe('/api/upload');
    expect(init.method).toBe('POST');
    expect((init.body as FormData).get('file')).toBeInstanceOf(File);
  });

  it('posts events as JSON to the dataset', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { version: 2 }));
    vi.stubGlobal('fetch', fetchMock);
    await postEvent('d 1', cancel);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/datasets/d%201/events');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body as string)).toEqual(cancel);
  });

  it('passes the plan kind to the geometry endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { legs: [] }));
    vi.stubGlobal('fetch', fetchMock);
    await getRouteGeometry('d1', 'E01', 'previous');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/datasets/d1/routes/E01/geometry?plan=previous');
  });

  it('turns backend detail into ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(409, { detail: 'Датасет ещё обрабатывается' })));
    await expect(buildPlan('d1')).rejects.toMatchObject({ status: 409, message: 'Датасет ещё обрабатывается' });
  });

  it('joins FastAPI validation errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(422, { detail: [{ msg: 'Field required' }, { msg: 'bad time' }] })));
    await expect(buildPlan('d1')).rejects.toMatchObject({ status: 422, message: 'Field required; bad time' });
  });

  it('reports a network failure as ApiError with status 0', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const error = await buildPlan('d1').catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0 });
  });
});
```

- [ ] **Step 6: Убедиться, что тест падает**

Run: `npx vitest run src/api/client.test.ts`
Expected: FAIL с `Error: Failed to resolve import "./client" from "src/api/client.test.ts". Does the file exist?`

- [ ] **Step 7: Реализовать `frontend/src/api/client.ts`**

```ts
import type {
  ClientConfig,
  DatasetStatus,
  Explanation,
  PlanEvent,
  PlanningState,
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

const dataset = (datasetId: string) => `/datasets/${encodeURIComponent(datasetId)}`;

export const getConfig = () => request<ClientConfig>('/config');

export const getHealth = () => request<{ status: string }>('/health');

export function uploadFile(file: File): Promise<DatasetStatus> {
  const form = new FormData();
  form.append('file', file);
  return request<DatasetStatus>('/upload', { method: 'POST', body: form });
}

export const getDatasetStatus = (datasetId: string) => request<DatasetStatus>(dataset(datasetId));

export const buildPlan = (datasetId: string) =>
  request<PlanningState>(`${dataset(datasetId)}/plan`, { method: 'POST' });

export const getPlanningState = (datasetId: string) => request<PlanningState>(`${dataset(datasetId)}/state`);

export const postEvent = (datasetId: string, event: PlanEvent) =>
  request<PlanningState>(`${dataset(datasetId)}/events`, postJson(event));

export const getExplanation = (datasetId: string, requestId: string) =>
  request<Explanation>(`${dataset(datasetId)}/explain/${encodeURIComponent(requestId)}`);

export const getRouteGeometry = (datasetId: string, engineerId: string, plan: 'current' | 'previous' = 'current') =>
  request<RouteGeometry>(`${dataset(datasetId)}/routes/${encodeURIComponent(engineerId)}/geometry?plan=${plan}`);
```

- [ ] **Step 8: Прогнать тест и проверку типов**

Run: `npx vitest run src/api/client.test.ts && npx tsc --noEmit`
Expected: `Tests  6 passed (6)`, затем `tsc` без вывода с кодом 0.

- [ ] **Step 9: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vite.config.ts frontend/index.html frontend/.gitignore frontend/src/test/setup.ts frontend/src/api
git commit -m "feat(frontend): каркас Vite, типы API-контракта и HTTP-клиент" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```

---

## Task 2: Форматирование и цвета инженеров

**Files:**
- Create: `frontend/src/lib/format.ts`, `frontend/src/lib/colors.ts`
- Test: `frontend/src/lib/format.test.ts`, `frontend/src/lib/colors.test.ts`

**Interfaces:**
- Consumes: типы из `src/api/types.ts`.
- Produces: `SKILL_LABELS`, `TRANSPORT_LABELS`, `PRIORITY_LABELS`, `STAGE_LABELS`, `SOLVER_LABELS`, `EVENT_LABELS`, `REASON_LABELS`, `PRECISION_LABELS`, `MATRIX_SOURCE_LABELS`; `isValidTime(value: string): boolean`, `toMinutes(value: HHMM): number`, `fromMinutes(total: number): HHMM`, `laterTime(a: HHMM, b: HHMM): HHMM`, `addMinutes(time: HHMM, minutes: number, cap?: number): HHMM`, `formatWindow(start: HHMM, end: HHMM): string`, `formatKm(km: number): string`, `formatSigned(value: number, digits?: number): string`, `shortAddress(address: string): string`. Из `colors.ts`: `ENGINEER_PALETTE: string[]`, `UNASSIGNED_COLOR: string`, `engineerColor(engineerId: string | null | undefined, engineerIds: string[]): string`.

- [ ] **Step 1: Написать падающие тесты**

`frontend/src/lib/format.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { addMinutes, formatKm, formatSigned, fromMinutes, isValidTime, laterTime, shortAddress, toMinutes } from './format';

describe('format', () => {
  it('converts HH:MM to minutes and back', () => {
    expect(toMinutes('09:30')).toBe(570);
    expect(toMinutes('0:01')).toBe(1);
    expect(fromMinutes(570)).toBe('09:30');
    expect(fromMinutes(1470)).toBe('24:30');
    expect(toMinutes('49:13')).toBe(2953);
    expect(toMinutes('125:00')).toBe(7500);
  });

  it('validates time strings', () => {
    expect(isValidTime('13:00')).toBe(true);
    expect(isValidTime('13:7')).toBe(false);
    expect(isValidTime('25:53')).toBe(true);
    expect(isValidTime('10:60')).toBe(false);
    expect(isValidTime('')).toBe(false);
    expect(() => toMinutes('abc')).toThrow('Некорректное время');
  });

  it('picks the later time and caps additions at the end of day', () => {
    expect(laterTime('12:00', '13:00')).toBe('13:00');
    expect(laterTime('14:00', '13:00')).toBe('14:00');
    expect(addMinutes('13:00', 120)).toBe('15:00');
    expect(addMinutes('23:00', 120)).toBe('23:59');
  });

  it('formats km and signed deltas in Russian style', () => {
    expect(formatKm(34.94)).toBe('34,9 км');
    expect(formatSigned(-2)).toBe('−2');
    expect(formatSigned(3.25, 1)).toBe('+3,3');
    expect(formatSigned(-0.01, 1)).toBe('0');
  });

  it('shortens Moscow addresses and drops the flat', () => {
    expect(shortAddress('Город Москва, ул.Юности, д. 32, кв. 5')).toBe('ул.Юности, д. 32');
    expect(shortAddress('г.Город Москва, наб.Семеновская, д. 3/1к2')).toBe('наб.Семеновская, д. 3/1к2');
    expect(shortAddress('Кашира, ул.Победы, д. 9')).toBe('Кашира, ул.Победы, д. 9');
  });
});
```

`frontend/src/lib/colors.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { engineerColor, ENGINEER_PALETTE, UNASSIGNED_COLOR } from './colors';

describe('engineerColor', () => {
  it('assigns colors by engineer position', () => {
    const ids = ['E01', 'E02'];
    expect(engineerColor('E01', ids)).toBe(ENGINEER_PALETTE[0]);
    expect(engineerColor('E02', ids)).toBe(ENGINEER_PALETTE[1]);
  });

  it('wraps around the palette and greys out unknown or missing engineers', () => {
    const ids = Array.from({ length: ENGINEER_PALETTE.length + 1 }, (_, index) => `E${index}`);
    expect(engineerColor(ids[ENGINEER_PALETTE.length], ids)).toBe(ENGINEER_PALETTE[0]);
    expect(engineerColor(null, ids)).toBe(UNASSIGNED_COLOR);
    expect(engineerColor('X', ids)).toBe(UNASSIGNED_COLOR);
  });
});
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `npx vitest run src/lib/format.test.ts src/lib/colors.test.ts`
Expected: FAIL с `Failed to resolve import "./format"` и `Failed to resolve import "./colors"`.

- [ ] **Step 3: Реализовать `frontend/src/lib/format.ts`**

```ts
import type {
  DatasetStage,
  EventType,
  GeocodePrecision,
  HHMM,
  MatrixSource,
  Priority,
  ReasonCode,
  Skill,
  SolverName,
  Transport,
} from '../api/types';

export const SKILL_LABELS: Record<Skill, string> = {
  local: 'Локальные работы',
  connection: 'Работы на подключение и дозаказы',
  emergency: 'Аварийные работы',
};

export const TRANSPORT_LABELS: Record<Transport, string> = {
  car: 'Автомобиль',
  foot: 'Пешеход',
  bike: 'Велосипед',
  public: 'Общественный транспорт',
};

export const PRIORITY_LABELS: Record<Priority, string> = { normal: 'Обычная', urgent: 'Срочная' };

export const STAGE_LABELS: Record<DatasetStage, string> = {
  parsing: 'Чтение файла',
  geocoding: 'Поиск адресов на карте',
  matrix: 'Расчёт расстояний',
  solving: 'Расчёт планов',
  ready: 'Готово',
};

export const SOLVER_LABELS: Record<SolverName, string> = {
  fcfs: 'Базовый (FCFS)',
  ortools: 'Оптимизированный',
  dispatchers: 'Диспетчеры',
};

export const EVENT_LABELS: Record<EventType, string> = {
  urgent: 'Срочная заявка',
  cancel: 'Отмена заявки',
  restore: 'Возврат заявки',
  engineer_unavailable: 'Инженер недоступен',
};

export const REASON_LABELS: Record<ReasonCode, string> = {
  no_skill: 'Нет навыка',
  no_transport: 'Нет транспорта',
  does_not_fit_window_or_shift: 'Не помещается в окно или смену',
  no_free_engineer_in_window: 'Нет свободных исполнителей',
  address_not_found: 'Адрес не найден',
};

export const PRECISION_LABELS: Record<GeocodePrecision, string> = {
  house: 'до дома',
  street: 'до улицы',
  locality: 'до района',
  none: 'не найдено',
};

export const MATRIX_SOURCE_LABELS: Record<MatrixSource, string> = {
  osrm: 'Дорожный граф OSRM',
  haversine: 'Прямые расстояния (OSRM недоступен)',
};

// Часы не ограничены 24: в плане диспетчеров встречаются значения вроде 25:53 и 49:13.
const TIME_RE = /^(\d+):(\d{2})$/;

export function isValidTime(value: string): boolean {
  const match = TIME_RE.exec(value.trim());
  return match !== null && Number(match[2]) < 60;
}

export function toMinutes(value: HHMM): number {
  const match = TIME_RE.exec(value.trim());
  if (!match || Number(match[2]) >= 60) {
    throw new Error(`Некорректное время: ${value}`);
  }
  return Number(match[1]) * 60 + Number(match[2]);
}

export function fromMinutes(total: number): HHMM {
  const safe = Math.max(0, Math.round(total));
  return `${String(Math.floor(safe / 60)).padStart(2, '0')}:${String(safe % 60).padStart(2, '0')}`;
}

export function laterTime(a: HHMM, b: HHMM): HHMM {
  return toMinutes(a) >= toMinutes(b) ? a : b;
}

export function addMinutes(time: HHMM, minutes: number, cap = 23 * 60 + 59): HHMM {
  return fromMinutes(Math.min(cap, toMinutes(time) + minutes));
}

export function formatWindow(start: HHMM, end: HHMM): string {
  return `${start}–${end}`;
}

export function formatKm(km: number): string {
  return `${km.toFixed(1).replace('.', ',')} км`;
}

export function formatSigned(value: number, digits = 0): string {
  const rounded = Number(value.toFixed(digits));
  if (rounded === 0) return '0';
  const text = Math.abs(rounded).toFixed(digits).replace('.', ',');
  return rounded > 0 ? `+${text}` : `−${text}`;
}

export function shortAddress(address: string): string {
  return address.replace(/^(г\.\s*)?(Город\s+)?Москва,\s*/u, '').replace(/,\s*кв\.\s*\S+\s*$/u, '');
}
```

- [ ] **Step 4: Реализовать `frontend/src/lib/colors.ts`**

```ts
// Красный зарезервирован под срочные заявки, серый под неназначенные.
export const ENGINEER_PALETTE = [
  '#2563eb',
  '#16a34a',
  '#ea580c',
  '#9333ea',
  '#0891b2',
  '#ca8a04',
  '#db2777',
  '#4f46e5',
  '#65a30d',
  '#0d9488',
  '#7c3aed',
  '#b45309',
  '#0284c7',
  '#be185d',
];
export const UNASSIGNED_COLOR = '#9ca3af';

export function engineerColor(engineerId: string | null | undefined, engineerIds: string[]): string {
  if (!engineerId) return UNASSIGNED_COLOR;
  const index = engineerIds.indexOf(engineerId);
  return index < 0 ? UNASSIGNED_COLOR : ENGINEER_PALETTE[index % ENGINEER_PALETTE.length];
}
```

- [ ] **Step 5: Прогнать тесты**

Run: `npx vitest run src/lib/format.test.ts src/lib/colors.test.ts`
Expected: `Test Files  2 passed (2)`, `Tests  7 passed (7)`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/format.ts frontend/src/lib/format.test.ts frontend/src/lib/colors.ts frontend/src/lib/colors.test.ts
git commit -m "feat(frontend): форматирование времени, км и цвета инженеров" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```

---

## Task 3: Контрактная фикстура, представление плана и события

**Files:**
- Create: `frontend/src/test/fixtures.ts`, `frontend/src/lib/planView.ts`, `frontend/src/lib/events.ts`
- Test: `frontend/src/lib/planView.test.ts`, `frontend/src/lib/events.test.ts`

**Interfaces:**
- Consumes: типы API, `toMinutes`, `isValidTime` из `format.ts`.
- Produces: фикстуры `makePlanningState(overrides?: Partial<PlanningState>): PlanningState`, `makeDatasetStatus(overrides?)`, `makeExplanation(overrides?)`, `makeRouteGeometry()`. Состояние фикстуры: регион «Восток», 3 инженера со своими стартами и сменой 10:00–22:00 (E03 «Бригада Комарь» недоступен с 13:00), 8 заявок (`URG-001` срочная, `10135` отменена, `18754` неназначенная), `now` = 13:00, версия 4, три события, `previous_plan`, `baseline`, `control` и `last_diff` (перенос 50104 от E02 к E01, новое назначение URG-001, сдвиг 46393 на 10 минут).
  Из `planView.ts`: `interface AssignmentInfo {engineerId; visit; order}`, `type DiffMark = 'added' | 'moved' | 'removed' | 'shifted'`, `DIFF_MARK_LABELS`, `displayedPlan(state, showPrevious): Plan`, `byId<T extends {id: string}>(items): Map<string, T>`, `engineerIdsOf(state): string[]`, `assignmentIndex(plan): Map<string, AssignmentInfo>`, `unassignedIndex(plan): Map<string, Unassigned>`, `routeRequestIds(plan, engineerId): string[]`, `diffMarks(diff: PlanDiff | null): Map<string, DiffMark>`, `straightLegs(route, engineer, requests: Map<string, ServiceRequest>): RouteLeg[]`, `sortRequestsForList(requests, plan): ServiceRequest[]`.
  Из `events.ts`: `interface PickedPoint {lat; lon}`, `interface UrgentForm {address; point; windowStart; windowEnd; durationMin; skill; transport: Transport | ''; time}`, `timeError(time, now): string | null`, `validateUrgentForm(form, now): string[]`, `newUrgentId(timestamp: number): string`, `buildUrgentEvent(form, requestId): PlanEvent`, `cancelEvent(requestId, time)`, `restoreEvent(requestId, time)`, `unavailableEvent(engineerId, time)`, `describeEvent(event, engineers: Map<string, Engineer>): string`.

- [ ] **Step 1: Создать фикстуру `frontend/src/test/fixtures.ts`**

Фикстура соответствует контракту и используется всеми следующими тестами. Координаты старта инженеров намеренно отличаются от офиса.

```ts
import type {
  DatasetStatus,
  Engineer,
  Explanation,
  Metrics,
  PlanningState,
  Route,
  RouteGeometry,
  ServiceRequest,
  Skill,
  Visit,
} from '../api/types';

const OFFICE = { region: 'east', title: 'Восток', address: 'г. Москва, ул Юных Ленинцев, д 83с 4', lat: 55.7075, lon: 37.7862 };

function visit(requestId: string, arrival: string, start: string, end: string, legKm: number, legMin: number, pinned = false): Visit {
  return { request_id: requestId, arrival, start, end, leg_km: legKm, leg_min: legMin, late_min: 0, pinned };
}

function route(engineerId: string, visits: Visit[]): Route {
  return {
    engineer_id: engineerId,
    visits,
    total_km: Number(visits.reduce((sum, item) => sum + item.leg_km, 0).toFixed(2)),
    total_travel_min: visits.reduce((sum, item) => sum + item.leg_min, 0),
  };
}

function metrics(routes: Route[], unassigned: number, violations = 0): Metrics {
  const used = routes.filter((item) => item.visits.length > 0);
  return {
    engineers_used: used.length,
    km_per_engineer: Object.fromEntries(used.map((item) => [item.engineer_id, item.total_km])),
    total_km: Number(used.reduce((sum, item) => sum + item.total_km, 0).toFixed(2)),
    assigned: used.reduce((sum, item) => sum + item.visits.length, 0),
    unassigned,
    violations,
  };
}

function request(
  id: string,
  address: string,
  lat: number,
  lon: number,
  skill: Skill,
  windowStart: string,
  windowEnd: string,
  durationMin: number,
  extra: Partial<ServiceRequest> = {},
): ServiceRequest {
  return {
    id,
    address,
    lat,
    lon,
    geocode_precision: 'house',
    district: 'Кузьминки',
    duration_min: durationMin,
    window_start: windowStart,
    window_end: windowEnd,
    priority: 'normal',
    skill,
    transport_required: null,
    status: 'active',
    source_type_bk: skill === 'local' ? 'Локальная заявка' : 'Подключение',
    source_type_hd: skill === 'local' ? 'Нет линка' : 'Конвергенция абонента',
    ...extra,
  };
}

function engineers(): Engineer[] {
  // Старт каждого инженера: медоид адресов его бригады, а не офис региона.
  return [
    { id: 'E01', name: 'Бригада Арташкин', start_lat: 55.7005, start_lon: 37.781, shift_start: '10:00', shift_end: '22:00', skills: ['local', 'connection'], transport: 'car', available: true, unavailable_from: null },
    { id: 'E02', name: 'Бригада Белузин', start_lat: 55.745, start_lon: 37.802, shift_start: '10:00', shift_end: '22:00', skills: ['local', 'connection', 'emergency'], transport: 'car', available: true, unavailable_from: null },
    { id: 'E03', name: 'Бригада Комарь', start_lat: 55.73, start_lon: 37.74, shift_start: '10:00', shift_end: '22:00', skills: ['local'], transport: 'foot', available: false, unavailable_from: '13:00' },
  ];
}

function requests(): ServiceRequest[] {
  return [
    request('74198', 'Город Москва, пр-кт.Волгоградский, д. 128 к 5', 55.7008, 37.7822, 'connection', '10:00', '12:00', 60),
    request('86160', 'Город Москва, пер.Маяковского, д. 2', 55.7431, 37.6612, 'connection', '12:00', '14:00', 60),
    request('50104', 'Город Москва, ул.Грайвороновская, д. 10 к 2', 55.7212, 37.7336, 'local', '14:00', '16:00', 45),
    request('46393', 'Город Москва, ул.Шарикоподшипниковская, д. 14', 55.7195, 37.68, 'local', '15:00', '17:00', 45),
    request('10135', 'Город Москва, ул.3-я Карачаровская, д. 5 к 2', 55.735, 37.751, 'local', '10:00', '12:00', 45, {
      status: 'cancelled',
    }),
    request('18754', 'Город Москва, ул.1-я Новокузьминская, д. 16 к 1', 55.716, 37.791, 'connection', '18:00', '20:00', 60),
    request('84627', 'Город Москва, ул.Юности, д. 32', 55.76, 37.805, 'local', '12:00', '14:00', 40),
    request('URG-001', 'Город Москва, ул.Ташкентская, д. 16к2', 55.712, 37.809, 'emergency', '13:00', '15:00', 60, {
      priority: 'urgent',
      transport_required: 'car',
      source_type_bk: 'Глобальная проблема',
      source_type_hd: 'Авария',
    }),
  ];
}

const UNASSIGNED_18754 = {
  request_id: '18754',
  reason_code: 'does_not_fit_window_or_shift' as const,
  reason_text:
    'Работа не помещается в окно 18:00–20:00 или в смену: даже без других заявок Бригада Белузин начнёт не раньше 20:10.',
};

export function makePlanningState(overrides: Partial<PlanningState> = {}): PlanningState {
  const currentRoutes = [
    route('E01', [
      visit('74198', '09:35', '10:00', '11:00', 6.1, 35, true),
      visit('86160', '11:40', '12:00', '13:00', 7.9, 40, true),
      visit('50104', '13:35', '14:00', '14:45', 5.8, 35),
      visit('46393', '15:10', '15:10', '15:55', 3.9, 25),
    ]),
    route('E02', [visit('84627', '09:25', '12:00', '12:40', 5.9, 25, true), visit('URG-001', '13:05', '13:05', '14:05', 5.3, 25)]),
    route('E03', []),
  ];
  const previousRoutes = [
    route('E01', [
      visit('74198', '09:35', '10:00', '11:00', 6.1, 35, true),
      visit('86160', '11:40', '12:00', '13:00', 7.9, 40, true),
      visit('46393', '13:30', '15:00', '15:45', 6.4, 30),
    ]),
    route('E02', [visit('84627', '09:25', '12:00', '12:40', 5.9, 25, true), visit('50104', '13:05', '14:00', '14:45', 5.4, 25)]),
    route('E03', []),
  ];
  const baselineRoutes = [
    route('E01', [
      visit('74198', '09:35', '10:00', '11:00', 6.1, 35, true),
      visit('86160', '11:40', '12:00', '13:00', 7.9, 40, true),
      visit('46393', '13:35', '15:00', '15:45', 7.0, 35),
    ]),
    route('E02', [visit('84627', '09:25', '12:00', '12:40', 5.9, 25, true), visit('URG-001', '13:05', '13:05', '14:05', 5.3, 25)]),
    route('E03', []),
  ];
  const controlRoutes = [
    route('E01', [visit('74198', '09:35', '10:00', '11:00', 6.1, 35), visit('86160', '11:40', '12:00', '13:00', 7.9, 40)]),
    route('E02', [
      visit('84627', '09:25', '12:00', '12:40', 5.9, 25),
      visit('50104', '13:05', '14:00', '14:45', 5.4, 25),
      visit('46393', '15:15', '15:15', '16:00', 4.9, 30),
    ]),
    route('E03', [visit('10135', '14:05', '14:05', '14:50', 4.3, 65), visit('18754', '16:00', '18:00', '19:00', 5.5, 70)]),
  ];
  const currentMetrics = metrics(currentRoutes, 1);
  const previousMetrics = metrics(previousRoutes, 1);
  return {
    dataset_id: 'd_test',
    version: 4,
    region: 'east',
    office: { ...OFFICE },
    now: '13:00',
    requests: requests(),
    engineers: engineers(),
    plan: { solver: 'ortools', routes: currentRoutes, unassigned: [{ ...UNASSIGNED_18754 }], metrics: currentMetrics, violations: [] },
    previous_plan: {
      solver: 'ortools',
      routes: previousRoutes,
      unassigned: [{ ...UNASSIGNED_18754 }],
      metrics: previousMetrics,
      violations: [],
    },
    baseline: {
      solver: 'fcfs',
      routes: baselineRoutes,
      unassigned: [
        { ...UNASSIGNED_18754 },
        {
          request_id: '50104',
          reason_code: 'no_free_engineer_in_window',
          reason_text: 'Нет свободных исполнителей на окно 14:00–16:00: подходящие инженеры (2) заняты другими заявками.',
        },
      ],
      metrics: metrics(baselineRoutes, 2),
      violations: [],
    },
    control: {
      solver: 'dispatchers',
      routes: controlRoutes,
      unassigned: [],
      metrics: metrics(controlRoutes, 0, 2),
      violations: ['10135: заявка отменена', '18754: окончание 19:00 позже конца смены 13:00'],
    },
    last_diff: {
      moved: [{ request_id: '50104', from_engineer_id: 'E02', to_engineer_id: 'E01' }],
      added: [{ request_id: 'URG-001', engineer_id: 'E02' }],
      removed: [],
      reordered_engineers: ['E01'],
      time_shifts: [{ request_id: '46393', engineer_id: 'E01', old_start: '15:00', new_start: '15:10', delta_min: 10 }],
      metrics_before: previousMetrics,
      metrics_after: currentMetrics,
    },
    events: [
      { id: 'ev_1', event: { type: 'cancel', time: '09:30', request: null, request_id: '10135', engineer_id: null }, version: 2 },
      { id: 'ev_2', event: { type: 'engineer_unavailable', time: '13:00', request: null, request_id: null, engineer_id: 'E03' }, version: 3 },
      {
        id: 'ev_3',
        event: { type: 'urgent', time: '13:00', request: requests()[7], request_id: null, engineer_id: null },
        version: 4,
      },
    ],
    matrix_source: 'osrm',
    ...overrides,
  };
}

export function makeDatasetStatus(overrides: Partial<DatasetStatus> = {}): DatasetStatus {
  return {
    dataset_id: 'd_test',
    status: 'ready',
    stage: 'ready',
    progress: { done: 66, total: 66 },
    report: {
      region: 'east',
      region_title: 'Восток',
      source: 'beeline_csv',
      requests: 66,
      engineers: 12,
      skipped_rows: ['строка 68: нет номера заявки или временного окна', 'строка 69: нет номера заявки или временного окна'],
      geocoding: { house: 50, street: 12, locality: 3, none: 1 },
      not_found: [{ request_id: '86160', address: 'Город Москва, пер.Маяковского, д. 2' }],
      matrix_source: 'osrm',
    },
    error: null,
    ...overrides,
  };
}

export function makeExplanation(overrides: Partial<Explanation> = {}): Explanation {
  return {
    request_id: '50104',
    status: 'assigned',
    engineer_id: 'E01',
    summary: 'Назначена Бригада Арташкин: после срочной заявки у Бригады Белузин нет времени, а Арташкин свободен после 13:00.',
    factors: ['Не нужен дополнительный инженер', 'Самая короткая вставка в маршрут'],
    constraints: [
      { name: 'Навык', ok: true, detail: 'Нужен «Локальные работы», у инженера есть' },
      { name: 'Транспорт', ok: true, detail: 'Требований к транспорту нет' },
      { name: 'Временное окно', ok: true, detail: 'Начало 14:00 внутри окна 14:00–16:00' },
      { name: 'Смена', ok: true, detail: 'Окончание 14:45, смена до 18:00' },
    ],
    visit: { request_id: '50104', arrival: '13:35', start: '14:00', end: '14:45', leg_km: 5.8, leg_min: 35, late_min: 0, pinned: false },
    alternatives: [
      { engineer_id: 'E02', feasible: true, extra_km: 2.1, start: '14:30', reason: 'Может взять, но пробег больше на 2,1 км' },
      { engineer_id: 'E03', feasible: false, extra_km: null, start: null, reason: 'Недоступен с 13:00' },
    ],
    unassigned: null,
    ...overrides,
  };
}

export function makeRouteGeometry(): RouteGeometry {
  return {
    engineer_id: 'E02',
    transport: 'car',
    source: 'osrm',
    legs: [
      { to_request_id: '84627', coordinates: [[37.802, 55.745], [37.803, 55.752], [37.805, 55.76]] },
      { to_request_id: 'URG-001', coordinates: [[37.805, 55.76], [37.809, 55.712]] },
    ],
  };
}
```

- [ ] **Step 2: Написать падающие тесты**

`frontend/src/lib/planView.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { makePlanningState } from '../test/fixtures';
import {
  assignmentIndex,
  byId,
  diffMarks,
  displayedPlan,
  routeRequestIds,
  sortRequestsForList,
  straightLegs,
  unassignedIndex,
} from './planView';

describe('planView', () => {
  const state = makePlanningState();

  it('shows the previous plan only when requested and available', () => {
    expect(displayedPlan(state, false)).toBe(state.plan);
    expect(displayedPlan(state, true)).toBe(state.previous_plan);
    const noPrevious = { ...state, previous_plan: null };
    expect(displayedPlan(noPrevious, true)).toBe(noPrevious.plan);
  });

  it('indexes assignments with visit order and unassigned reasons', () => {
    const index = assignmentIndex(state.plan);
    expect(index.get('50104')).toMatchObject({ engineerId: 'E01', order: 2 });
    expect(index.has('18754')).toBe(false);
    expect(unassignedIndex(state.plan).get('18754')?.reason_code).toBe('does_not_fit_window_or_shift');
  });

  it('marks diff with added before moved before time shifts', () => {
    const marks = diffMarks(state.last_diff);
    expect(marks.get('URG-001')).toBe('added');
    expect(marks.get('50104')).toBe('moved');
    expect(marks.get('46393')).toBe('shifted');
    expect(diffMarks(null).size).toBe(0);
  });

  it('builds straight legs from the engineer start through visits', () => {
    const legs = straightLegs(state.plan.routes[1], state.engineers[1], byId(state.requests));
    expect(legs.map((leg) => leg.to_request_id)).toEqual(['84627', 'URG-001']);
    expect(legs[0].coordinates[0]).toEqual([state.engineers[1].start_lon, state.engineers[1].start_lat]);
    expect(legs[0].coordinates[0]).not.toEqual([state.office.lon, state.office.lat]);
    expect(legs[1].coordinates[0]).toEqual([37.805, 55.76]);
  });

  it('sorts the list: assigned by start, then unassigned, cancelled last', () => {
    const ids = sortRequestsForList(state.requests, state.plan).map((request) => request.id);
    expect(ids).toEqual(['74198', '84627', '86160', 'URG-001', '50104', '46393', '18754', '10135']);
  });

  it('returns route order for one engineer', () => {
    expect(routeRequestIds(state.plan, 'E01')).toEqual(['74198', '86160', '50104', '46393']);
    expect(routeRequestIds(state.plan, 'E03')).toEqual([]);
  });
});
```

`frontend/src/lib/events.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { makePlanningState } from '../test/fixtures';
import {
  buildUrgentEvent,
  cancelEvent,
  describeEvent,
  newUrgentId,
  restoreEvent,
  timeError,
  unavailableEvent,
  validateUrgentForm,
  type UrgentForm,
} from './events';
import { byId } from './planView';

const form: UrgentForm = {
  address: 'Город Москва, ул.Ташкентская, д. 16к2',
  point: null,
  windowStart: '13:00',
  windowEnd: '15:00',
  durationMin: 60,
  skill: 'emergency',
  transport: 'car',
  time: '13:00',
};

describe('events', () => {
  it('accepts a valid urgent form', () => {
    expect(validateUrgentForm(form, '13:00')).toEqual([]);
  });

  it('reports every problem in Russian', () => {
    const errors = validateUrgentForm({ ...form, address: ' ', windowEnd: '12:00', durationMin: 0, time: '12:59' }, '13:00');
    expect(errors).toEqual([
      'Укажите адрес или точку на карте',
      'Конец окна должен быть позже начала',
      'Длительность должна быть больше нуля',
      'Время события не может быть раньше 13:00',
    ]);
    expect(timeError('9', '13:00')).toBe('Укажите время в формате ЧЧ:ММ');
  });

  it('builds an urgent event with a full request from a map point', () => {
    const event = buildUrgentEvent({ ...form, address: '', point: { lat: 55.71, lon: 37.8 }, transport: '' }, 'URG-TEST');
    expect(event).toMatchObject({ type: 'urgent', time: '13:00', request_id: null, engineer_id: null });
    expect(event.request).toMatchObject({
      id: 'URG-TEST',
      lat: 55.71,
      lon: 37.8,
      priority: 'urgent',
      status: 'active',
      transport_required: null,
      geocode_precision: 'house',
      window_start: '13:00',
      window_end: '15:00',
    });
    expect(event.request?.address).toBe('Точка на карте 55.71000, 37.80000');
  });

  it('builds cancel, restore and unavailability events', () => {
    expect(cancelEvent('1', '14:00')).toEqual({ type: 'cancel', time: '14:00', request: null, request_id: '1', engineer_id: null });
    expect(restoreEvent('1', '14:00').type).toBe('restore');
    expect(unavailableEvent('E01', '14:00')).toMatchObject({ type: 'engineer_unavailable', engineer_id: 'E01', request_id: null });
  });

  it('generates readable urgent ids', () => {
    expect(newUrgentId(1789430400000)).toMatch(/^URG-[0-9A-Z]+$/);
  });

  it('describes applied events for the dispatcher', () => {
    const engineers = byId(makePlanningState().engineers);
    expect(describeEvent(unavailableEvent('E03', '13:00'), engineers)).toBe('Инженер недоступен: Бригада Комарь с 13:00');
    expect(describeEvent(cancelEvent('10135', '09:30'), engineers)).toBe('Отмена заявки 10135 в 09:30');
  });
});
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `npx vitest run src/lib/planView.test.ts src/lib/events.test.ts`
Expected: FAIL с `Failed to resolve import "./planView"` и `Failed to resolve import "./events"`.

- [ ] **Step 4: Реализовать `frontend/src/lib/planView.ts`**

```ts
import type { Engineer, Plan, PlanDiff, PlanningState, Route, RouteLeg, ServiceRequest, Unassigned, Visit } from '../api/types';
import { toMinutes } from './format';

export interface AssignmentInfo {
  engineerId: string;
  visit: Visit;
  order: number;
}

export type DiffMark = 'added' | 'moved' | 'removed' | 'shifted';

export const DIFF_MARK_LABELS: Record<DiffMark, string> = {
  added: 'Новое назначение',
  moved: 'Перенесена',
  removed: 'Снята',
  shifted: 'Сдвиг времени',
};

export function displayedPlan(state: PlanningState, showPrevious: boolean): Plan {
  return showPrevious && state.previous_plan ? state.previous_plan : state.plan;
}

export function byId<T extends { id: string }>(items: T[]): Map<string, T> {
  return new Map(items.map((item) => [item.id, item]));
}

export function engineerIdsOf(state: PlanningState): string[] {
  return state.engineers.map((engineer) => engineer.id);
}

export function assignmentIndex(plan: Plan): Map<string, AssignmentInfo> {
  const index = new Map<string, AssignmentInfo>();
  for (const route of plan.routes) {
    route.visits.forEach((visit, order) => index.set(visit.request_id, { engineerId: route.engineer_id, visit, order }));
  }
  return index;
}

export function unassignedIndex(plan: Plan): Map<string, Unassigned> {
  return new Map(plan.unassigned.map((item) => [item.request_id, item]));
}

export function routeRequestIds(plan: Plan, engineerId: string): string[] {
  return plan.routes.find((route) => route.engineer_id === engineerId)?.visits.map((visit) => visit.request_id) ?? [];
}

export function diffMarks(diff: PlanDiff | null): Map<string, DiffMark> {
  const marks = new Map<string, DiffMark>();
  if (!diff) return marks;
  const put = (requestId: string, mark: DiffMark) => {
    if (!marks.has(requestId)) marks.set(requestId, mark);
  };
  diff.added.forEach((item) => put(item.request_id, 'added'));
  diff.moved.forEach((item) => put(item.request_id, 'moved'));
  diff.removed.forEach((item) => put(item.request_id, 'removed'));
  diff.time_shifts.filter((item) => item.delta_min !== 0).forEach((item) => put(item.request_id, 'shifted'));
  return marks;
}

/** Прямые отрезки от старта инженера через заявки: запасной вариант, пока нет геометрии OSRM. */
export function straightLegs(route: Route, engineer: Engineer, requests: Map<string, ServiceRequest>): RouteLeg[] {
  const legs: RouteLeg[] = [];
  let previous: [number, number] = [engineer.start_lon, engineer.start_lat];
  for (const visit of route.visits) {
    const request = requests.get(visit.request_id);
    if (!request || request.lat === null || request.lon === null) continue;
    const point: [number, number] = [request.lon, request.lat];
    legs.push({ to_request_id: visit.request_id, coordinates: [previous, point] });
    previous = point;
  }
  return legs;
}

/** Порядок списка: назначенные по времени начала, затем неназначенные по окну, отменённые в конце. */
export function sortRequestsForList(requests: ServiceRequest[], plan: Plan): ServiceRequest[] {
  const assigned = assignmentIndex(plan);
  const rank = (request: ServiceRequest) => (request.status === 'cancelled' ? 2 : assigned.has(request.id) ? 0 : 1);
  const time = (request: ServiceRequest) => toMinutes(assigned.get(request.id)?.visit.start ?? request.window_start);
  return [...requests].sort((a, b) => rank(a) - rank(b) || time(a) - time(b) || a.id.localeCompare(b.id));
}
```

- [ ] **Step 5: Реализовать `frontend/src/lib/events.ts`**

```ts
import type { Engineer, HHMM, PlanEvent, ServiceRequest, Skill, Transport } from '../api/types';
import { isValidTime, toMinutes } from './format';

export interface PickedPoint {
  lat: number;
  lon: number;
}

export interface UrgentForm {
  address: string;
  point: PickedPoint | null;
  windowStart: HHMM;
  windowEnd: HHMM;
  durationMin: number;
  skill: Skill;
  transport: Transport | '';
  time: HHMM;
}

export function timeError(time: string, now: HHMM): string | null {
  if (!isValidTime(time)) return 'Укажите время в формате ЧЧ:ММ';
  if (toMinutes(time) < toMinutes(now)) return `Время события не может быть раньше ${now}`;
  return null;
}

export function validateUrgentForm(form: UrgentForm, now: HHMM): string[] {
  const errors: string[] = [];
  if (!form.address.trim() && !form.point) errors.push('Укажите адрес или точку на карте');
  if (!isValidTime(form.windowStart) || !isValidTime(form.windowEnd)) {
    errors.push('Укажите окно визита в формате ЧЧ:ММ');
  } else if (toMinutes(form.windowEnd) <= toMinutes(form.windowStart)) {
    errors.push('Конец окна должен быть позже начала');
  }
  if (!Number.isFinite(form.durationMin) || form.durationMin <= 0) errors.push('Длительность должна быть больше нуля');
  const time = timeError(form.time, now);
  if (time) errors.push(time);
  return errors;
}

export function newUrgentId(timestamp: number): string {
  return `URG-${timestamp.toString(36).toUpperCase()}`;
}

export function buildUrgentEvent(form: UrgentForm, requestId: string): PlanEvent {
  const point = form.point;
  const address =
    form.address.trim() || (point ? `Точка на карте ${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}` : 'Срочная заявка');
  const request: ServiceRequest = {
    id: requestId,
    address,
    lat: point?.lat ?? null,
    lon: point?.lon ?? null,
    geocode_precision: point ? 'house' : 'none',
    district: '',
    duration_min: Math.round(form.durationMin),
    window_start: form.windowStart,
    window_end: form.windowEnd,
    priority: 'urgent',
    skill: form.skill,
    transport_required: form.transport === '' ? null : form.transport,
    status: 'active',
    source_type_bk: 'Срочная заявка диспетчера',
    source_type_hd: '',
  };
  return { type: 'urgent', time: form.time, request, request_id: null, engineer_id: null };
}

export const cancelEvent = (requestId: string, time: HHMM): PlanEvent => ({
  type: 'cancel',
  time,
  request: null,
  request_id: requestId,
  engineer_id: null,
});

export const restoreEvent = (requestId: string, time: HHMM): PlanEvent => ({
  type: 'restore',
  time,
  request: null,
  request_id: requestId,
  engineer_id: null,
});

export const unavailableEvent = (engineerId: string, time: HHMM): PlanEvent => ({
  type: 'engineer_unavailable',
  time,
  request: null,
  request_id: null,
  engineer_id: engineerId,
});

export function describeEvent(event: PlanEvent, engineers: Map<string, Engineer>): string {
  switch (event.type) {
    case 'urgent':
      return `Срочная заявка ${event.request?.id ?? ''} в ${event.time}`;
    case 'cancel':
      return `Отмена заявки ${event.request_id} в ${event.time}`;
    case 'restore':
      return `Возврат заявки ${event.request_id} в ${event.time}`;
    case 'engineer_unavailable':
      return `Инженер недоступен: ${engineers.get(event.engineer_id ?? '')?.name ?? event.engineer_id} с ${event.time}`;
  }
}
```

- [ ] **Step 6: Прогнать тесты и проверку типов**

Run: `npx vitest run src/lib/planView.test.ts src/lib/events.test.ts && npx tsc --noEmit`
Expected: `Test Files  2 passed (2)`, `Tests  12 passed (12)`, `tsc` без вывода.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/test/fixtures.ts frontend/src/lib/planView.ts frontend/src/lib/planView.test.ts frontend/src/lib/events.ts frontend/src/lib/events.test.ts
git commit -m "feat(frontend): представление плана, пометки diff и сборка событий" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```

---

## Task 4: Расчёт таймлайна и таблицы сравнения

**Files:**
- Create: `frontend/src/lib/timeline.ts`, `frontend/src/lib/comparison.ts`
- Test: `frontend/src/lib/timeline.test.ts`, `frontend/src/lib/comparison.test.ts`

**Interfaces:**
- Consumes: `toMinutes`, `formatKm`, `formatSigned`, фикстура `makePlanningState`.
- Produces: из `timeline.ts` константы `AXIS_DEFAULT_FROM = 480`, `AXIS_DEFAULT_TO = 1380`, `AXIS_MAX = 1440`, типы `TimeScale {from; to}`, `TimelineBar {requestId; left; width; windowLeft; windowWidth; pinned; urgent; late; clipped; label}`, `TimelineRow {engineer; shiftLeft; shiftWidth; unavailableLeft; bars}`, функции `timeScale(state, plan): TimeScale`, `percent(scale, minutes): number`, `hourTicks(scale): number[]`, `timelineRows(state, plan, scale): TimelineRow[]`. Из `comparison.ts`: `type Verdict = 'better' | 'worse' | 'same'`, `ComparisonRow {label; baseline; optimized; dispatchers; delta; verdict}`, `KmRow {engineerId; name; baseline; optimized; dispatchers}`, `metricRows(state): ComparisonRow[]`, `kmPerEngineerRows(state): KmRow[]`.

- [ ] **Step 1: Написать падающие тесты**

`frontend/src/lib/timeline.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { makePlanningState } from '../test/fixtures';
import { hourTicks, percent, timelineRows, timeScale } from './timeline';

describe('timeline', () => {
  const state = makePlanningState();

  it('uses the default 08:00–23:00 axis when data fits inside it', () => {
    expect(timeScale(state, state.plan)).toEqual({ from: 480, to: 1380 });
  });

  it('extends the axis to fit data but never beyond 00:00–24:00', () => {
    const early = { ...state, engineers: state.engineers.map((engineer) => ({ ...engineer, shift_start: '06:30' })) };
    expect(timeScale(early, state.plan).from).toBe(360);

    const route = state.plan.routes[0];
    const overnight = {
      ...state.plan,
      routes: [{ ...route, visits: [{ ...route.visits[0], start: '23:10', end: '49:13' }] }, ...state.plan.routes.slice(1)],
    };
    expect(timeScale(state, overnight)).toEqual({ from: 480, to: 1440 });
  });

  it('converts minutes to clamped percentages', () => {
    const scale = { from: 540, to: 1320 };
    expect(percent(scale, 540)).toBe(0);
    expect(percent(scale, 930)).toBe(50);
    expect(percent(scale, 2000)).toBe(100);
    expect(percent(scale, 0)).toBe(0);
  });

  it('builds rows with bars, windows, pinned flags and unavailability', () => {
    const scale = timeScale(state, state.plan);
    const rows = timelineRows(state, state.plan, scale);
    expect(rows[0].bars.map((bar) => bar.requestId)).toEqual(['74198', '86160', '50104', '46393']);
    expect(rows[0].bars[0].pinned).toBe(true);
    expect(rows[0].bars[0].clipped).toBe(false);
    expect(rows[0].bars[2].windowLeft).toBeCloseTo(percent(scale, 840));
    expect(rows[1].bars[1].urgent).toBe(true);
    expect(rows[0].unavailableLeft).toBeNull();
    expect(rows[2].unavailableLeft).toBeCloseTo(percent(scale, 780));
  });

  it('clamps visits past midnight and keeps raw values in the label', () => {
    const route = state.plan.routes[0];
    const plan = {
      ...state.plan,
      solver: 'dispatchers' as const,
      routes: [{ ...route, visits: [{ ...route.visits[0], start: '25:53', end: '49:13' }] }, ...state.plan.routes.slice(1)],
    };
    const scale = timeScale(state, plan);
    const bar = timelineRows(state, plan, scale)[0].bars[0];
    expect(bar).toMatchObject({ clipped: true, label: '25:53–49:13' });
    expect(bar.left + bar.width).toBeLessThanOrEqual(100);
  });

  it('produces hour ticks', () => {
    expect(hourTicks({ from: 540, to: 720 })).toEqual([540, 600, 660, 720]);
  });
});
```

`frontend/src/lib/comparison.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { makePlanningState } from '../test/fixtures';
import { kmPerEngineerRows, metricRows } from './comparison';

describe('comparison', () => {
  const state = makePlanningState();

  it('compares the optimized plan with baseline and dispatchers', () => {
    const rows = metricRows(state);
    expect(rows.find((row) => row.label === 'Задействовано инженеров')).toMatchObject({
      baseline: '2',
      optimized: '2',
      dispatchers: '3',
      delta: '0',
      verdict: 'same',
    });
    expect(rows.find((row) => row.label === 'Не назначено')).toMatchObject({
      baseline: '2',
      optimized: '1',
      delta: '−1',
      verdict: 'better',
    });
    expect(rows.find((row) => row.label === 'Суммарный пробег')).toMatchObject({
      baseline: '32,2 км',
      optimized: '34,9 км',
      delta: '+2,7 км',
      verdict: 'worse',
    });
    expect(rows.find((row) => row.label === 'Нарушений ограничений')?.dispatchers).toBe('2');
  });

  it('shows missing dispatcher data', () => {
    expect(metricRows({ ...state, control: null })[0].dispatchers).toBe('нет данных');
  });

  it('lists km per engineer across all plans', () => {
    const rows = kmPerEngineerRows(state);
    expect(rows.map((row) => row.engineerId)).toEqual(['E01', 'E02', 'E03']);
    expect(rows[2]).toMatchObject({ baseline: null, optimized: null, dispatchers: 9.8 });
  });
});
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `npx vitest run src/lib/timeline.test.ts src/lib/comparison.test.ts`
Expected: FAIL с `Failed to resolve import "./timeline"` и `Failed to resolve import "./comparison"`.

- [ ] **Step 3: Реализовать `frontend/src/lib/timeline.ts`**

```ts
import type { Engineer, Plan, PlanningState } from '../api/types';
import { toMinutes } from './format';

/** Ось по умолчанию 08:00–23:00, расширяется под данные, но не дальше 00:00–24:00. */
export const AXIS_DEFAULT_FROM = 8 * 60;
export const AXIS_DEFAULT_TO = 23 * 60;
export const AXIS_MAX = 24 * 60;

export interface TimeScale {
  from: number;
  to: number;
}

export interface TimelineBar {
  requestId: string;
  left: number;
  width: number;
  windowLeft: number;
  windowWidth: number;
  pinned: boolean;
  urgent: boolean;
  late: boolean;
  /** Визит выходит за видимую шкалу (например 49:13 в плане диспетчеров) */
  clipped: boolean;
  /** Сырые значения HH:MM для подсказки */
  label: string;
}

export interface TimelineRow {
  engineer: Engineer;
  shiftLeft: number;
  shiftWidth: number;
  unavailableLeft: number | null;
  bars: TimelineBar[];
}

export function timeScale(state: PlanningState, plan: Plan): TimeScale {
  const starts = state.engineers.map((engineer) => toMinutes(engineer.shift_start));
  const ends = state.engineers.map((engineer) => toMinutes(engineer.shift_end));
  for (const route of plan.routes) {
    for (const visit of route.visits) {
      starts.push(toMinutes(visit.start));
      ends.push(toMinutes(visit.end));
    }
  }
  const from = Math.max(0, Math.floor(Math.min(AXIS_DEFAULT_FROM, ...starts) / 60) * 60);
  const to = Math.min(AXIS_MAX, Math.ceil(Math.max(AXIS_DEFAULT_TO, ...ends) / 60) * 60);
  return { from, to: Math.max(to, from + 60) };
}

export function percent(scale: TimeScale, minutes: number): number {
  const value = ((minutes - scale.from) / (scale.to - scale.from)) * 100;
  return Math.min(100, Math.max(0, value));
}

export function hourTicks(scale: TimeScale): number[] {
  const ticks: number[] = [];
  for (let minute = scale.from; minute <= scale.to; minute += 60) ticks.push(minute);
  return ticks;
}

export function timelineRows(state: PlanningState, plan: Plan, scale: TimeScale): TimelineRow[] {
  const requests = new Map(state.requests.map((request) => [request.id, request]));
  return state.engineers.map((engineer) => {
    const route = plan.routes.find((item) => item.engineer_id === engineer.id);
    const shiftStart = percent(scale, toMinutes(engineer.shift_start));
    const shiftEnd = percent(scale, toMinutes(engineer.shift_end));
    const bars = (route?.visits ?? []).map((visit) => {
      const request = requests.get(visit.request_id);
      const start = toMinutes(visit.start);
      const end = toMinutes(visit.end);
      const left = percent(scale, start);
      const right = percent(scale, end);
      const windowLeft = percent(scale, toMinutes(request?.window_start ?? visit.start));
      const windowRight = percent(scale, toMinutes(request?.window_end ?? visit.end));
      return {
        requestId: visit.request_id,
        left: Math.min(left, 99.5),
        width: Math.max(0.5, right - left),
        windowLeft,
        windowWidth: windowRight - windowLeft,
        pinned: visit.pinned,
        urgent: request?.priority === 'urgent',
        late: visit.late_min > 0,
        clipped: start < scale.from || end > scale.to,
        label: `${visit.start}–${visit.end}`,
      };
    });
    const unavailableFrom = engineer.available ? null : (engineer.unavailable_from ?? engineer.shift_start);
    return {
      engineer,
      shiftLeft: shiftStart,
      shiftWidth: shiftEnd - shiftStart,
      unavailableLeft: unavailableFrom === null ? null : percent(scale, toMinutes(unavailableFrom)),
      bars,
    };
  });
}
```

- [ ] **Step 4: Реализовать `frontend/src/lib/comparison.ts`**

Реальные значения для ориентира по ширине таблицы (Восток, матрица по прямым): FCFS 12 инженеров, 10 неназначенных, 421,95 км; OR-Tools 7, 0, 150,12 км; диспетчеры 12, 2, 220,65 км с нарушениями. Пробег бывает до ~800 км, нарушений до ~80. Числа в код не зашиваются.

```ts
import type { Metrics, PlanningState } from '../api/types';
import { formatKm, formatSigned } from './format';

type MetricKey = 'engineers_used' | 'total_km' | 'assigned' | 'unassigned' | 'violations';

interface MetricSpec {
  key: MetricKey;
  label: string;
  higherIsBetter: boolean;
  km: boolean;
}

const SPECS: MetricSpec[] = [
  { key: 'engineers_used', label: 'Задействовано инженеров', higherIsBetter: false, km: false },
  { key: 'total_km', label: 'Суммарный пробег', higherIsBetter: false, km: true },
  { key: 'assigned', label: 'Назначено заявок', higherIsBetter: true, km: false },
  { key: 'unassigned', label: 'Не назначено', higherIsBetter: false, km: false },
  { key: 'violations', label: 'Нарушений ограничений', higherIsBetter: false, km: false },
];

export type Verdict = 'better' | 'worse' | 'same';

export interface ComparisonRow {
  label: string;
  baseline: string;
  optimized: string;
  dispatchers: string;
  delta: string;
  verdict: Verdict;
}

export interface KmRow {
  engineerId: string;
  name: string;
  baseline: number | null;
  optimized: number | null;
  dispatchers: number | null;
}

function cell(metrics: Metrics | null, spec: MetricSpec): string {
  if (metrics === null) return 'нет данных';
  return spec.km ? formatKm(metrics[spec.key]) : String(metrics[spec.key]);
}

export function metricRows(state: PlanningState): ComparisonRow[] {
  return SPECS.map((spec) => {
    const diff = state.plan.metrics[spec.key] - state.baseline.metrics[spec.key];
    const rounded = Number(diff.toFixed(spec.km ? 1 : 0));
    const verdict: Verdict = rounded === 0 ? 'same' : rounded > 0 === spec.higherIsBetter ? 'better' : 'worse';
    return {
      label: spec.label,
      baseline: cell(state.baseline.metrics, spec),
      optimized: cell(state.plan.metrics, spec),
      dispatchers: cell(state.control?.metrics ?? null, spec),
      delta: spec.km ? `${formatSigned(diff, 1)} км` : formatSigned(diff),
      verdict,
    };
  });
}

export function kmPerEngineerRows(state: PlanningState): KmRow[] {
  const km = (metrics: Metrics | undefined, engineerId: string) => metrics?.km_per_engineer[engineerId] ?? null;
  return state.engineers
    .map((engineer) => ({
      engineerId: engineer.id,
      name: engineer.name,
      baseline: km(state.baseline.metrics, engineer.id),
      optimized: km(state.plan.metrics, engineer.id),
      dispatchers: km(state.control?.metrics, engineer.id),
    }))
    .filter((row) => row.baseline !== null || row.optimized !== null || row.dispatchers !== null);
}
```

- [ ] **Step 5: Прогнать тесты**

Run: `npx vitest run src/lib/timeline.test.ts src/lib/comparison.test.ts`
Expected: `Test Files  2 passed (2)`, `Tests  9 passed (9)`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/timeline.ts frontend/src/lib/timeline.test.ts frontend/src/lib/comparison.ts frontend/src/lib/comparison.test.ts
git commit -m "feat(frontend): шкала таймлайна и строки сравнения планов" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```

---

## Task 5: Стор приложения

**Files:**
- Create: `frontend/src/store/useAppStore.ts`, `frontend/src/test/store.ts`
- Test: `frontend/src/store/useAppStore.test.ts`

**Interfaces:**
- Consumes: функции `client.ts`, `laterTime`, `isValidTime`, тип `PickedPoint`.
- Produces: `POLL_INTERVAL_MS = 1000`; `interface AppData {config; datasetId; datasetStatus; state; selectedRequestId; selectedEngineerId; activeTab; showPrevious; eventTime; busy; error; pickMode; pickedPoint}`; `interface AppActions {loadConfig(): Promise<void>; upload(file: File): Promise<void>; plan(): Promise<void>; applyEvent(event: PlanEvent): Promise<boolean>; setPlanningState(state: PlanningState): void; selectRequest(id: string | null): void; selectEngineer(id: string | null): void; setTab(tabId: string): void; setShowPrevious(value: boolean): void; setEventTime(value: HHMM): void; startPick(): void; finishPick(point: PickedPoint | null): void; clearError(): void; reset(): void}`; `type AppState = AppData & AppActions`; `initialAppData: AppData`; хук `useAppStore`. Тестовый помощник `resetStore(patch?: Partial<AppState>): void`. План 4 применяет состояние после approve через `setPlanningState`.

- [ ] **Step 1: Написать помощник сброса `frontend/src/test/store.ts`**

```ts
import { initialAppData, useAppStore, type AppState } from '../store/useAppStore';

const pristine = useAppStore.getState();

/** Полный сброс стора между тестами, включая подменённые действия. */
export function resetStore(patch: Partial<AppState> = {}): void {
  useAppStore.setState({ ...pristine, ...initialAppData, ...patch }, true);
}
```

- [ ] **Step 2: Написать падающий тест `frontend/src/store/useAppStore.test.ts`**

```ts
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
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `npx vitest run src/store/useAppStore.test.ts`
Expected: FAIL с `Failed to resolve import` для `useAppStore`.

- [ ] **Step 4: Реализовать `frontend/src/store/useAppStore.ts`**

```ts
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
```

- [ ] **Step 5: Прогнать тест**

Run: `npx vitest run src/store/useAppStore.test.ts`
Expected: `Tests  8 passed (8)`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/store frontend/src/test/store.ts
git commit -m "feat(frontend): стор с опросом предподсчёта, планом и событиями" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```

---

## Task 6: Экран загрузки

**Files:**
- Create: `frontend/src/components/UploadScreen.tsx`
- Test: `frontend/src/components/UploadScreen.test.tsx`

**Interfaces:**
- Consumes: `useAppStore` (`datasetStatus`, `busy`, `error`, `upload`, `plan`), `STAGE_LABELS`, `PRECISION_LABELS`, `MATRIX_SOURCE_LABELS`.
- Produces: компонент `UploadScreen()` без пропсов. Скрытый `input[type=file]` помечен `data-testid="file-input"`.

- [ ] **Step 1: Написать падающий тест**

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, uploadFile: vi.fn(), getDatasetStatus: vi.fn(), buildPlan: vi.fn() };
});

import * as api from '../api/client';
import { useAppStore } from '../store/useAppStore';
import { makeDatasetStatus, makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { UploadScreen } from './UploadScreen';

beforeEach(() => {
  vi.resetAllMocks();
  resetStore();
});

describe('UploadScreen', () => {
  it('uploads a chosen file, shows the report and builds the plan', async () => {
    vi.mocked(api.uploadFile).mockResolvedValue(makeDatasetStatus());
    vi.mocked(api.buildPlan).mockResolvedValue(makePlanningState());
    render(<UploadScreen />);

    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['x'], 'east.csv')] } });

    expect(await screen.findByRole('heading', { name: 'Восток' })).toBeInTheDocument();
    expect(screen.getByText('Файл: east.csv')).toBeInTheDocument();
    expect(screen.getByText('Не найдены на карте: 1')).toBeInTheDocument();
    expect(screen.getByText('Пропущено строк: 2')).toBeInTheDocument();
    expect(screen.getByText('Дорожный граф OSRM')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Спланировать' }));
    await waitFor(() => expect(useAppStore.getState().state?.version).toBe(4));
  });

  it('shows the processing stage and progress without the plan button', () => {
    resetStore({
      datasetStatus: makeDatasetStatus({ status: 'processing', stage: 'geocoding', progress: { done: 12, total: 66 }, report: null }),
    });
    render(<UploadScreen />);
    expect(screen.getByText('Поиск адресов на карте')).toBeInTheDocument();
    expect(screen.getByText('12 из 66')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Спланировать' })).not.toBeInTheDocument();
  });

  it('shows the backend error', () => {
    resetStore({ error: 'В файле нет колонок: Адрес' });
    render(<UploadScreen />);
    expect(screen.getByRole('alert')).toHaveTextContent('В файле нет колонок: Адрес');
  });
});
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `npx vitest run src/components/UploadScreen.test.tsx`
Expected: FAIL с `Failed to resolve import "./UploadScreen"`.

- [ ] **Step 3: Реализовать `frontend/src/components/UploadScreen.tsx`**

```tsx
import { useRef, useState, type DragEvent } from 'react';
import type { GeocodePrecision } from '../api/types';
import { MATRIX_SOURCE_LABELS, PRECISION_LABELS, STAGE_LABELS } from '../lib/format';
import { useAppStore } from '../store/useAppStore';

const PRECISIONS: GeocodePrecision[] = ['house', 'street', 'locality', 'none'];

export function UploadScreen() {
  const status = useAppStore((s) => s.datasetStatus);
  const busy = useAppStore((s) => s.busy);
  const error = useAppStore((s) => s.error);
  const upload = useAppStore((s) => s.upload);
  const plan = useAppStore((s) => s.plan);
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const start = (file: File | undefined) => {
    if (!file) return;
    setFileName(file.name);
    void upload(file);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    start(event.dataTransfer.files[0]);
  };

  const processing = status?.status === 'processing';
  const report = status?.report ?? null;
  const progress = status && status.progress.total > 0 ? Math.round((status.progress.done / status.progress.total) * 100) : 0;

  return (
    <main className="upload-screen">
      <section className="upload-card">
        <h1>Планирование маршрутов выездных инженеров</h1>
        <p className="muted">
          Загрузите выгрузку заявок Билайна (CSV) или готовый набор данных (JSON). Сервис найдёт адреса на карте,
          посчитает расстояния и построит план на день.
        </p>

        <div
          className={`dropzone${dragging ? ' dropzone--active' : ''}`}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <p>Перетащите файл сюда или</p>
          <button type="button" className="btn btn-primary" onClick={() => inputRef.current?.click()} disabled={processing}>
            Загрузить CSV или JSON
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.json"
            hidden
            data-testid="file-input"
            onChange={(event) => start(event.target.files?.[0])}
          />
          {fileName && <p className="muted">Файл: {fileName}</p>}
        </div>

        {status && (
          <div className="upload-status" aria-live="polite">
            <div className="upload-status__row">
              <span>{STAGE_LABELS[status.stage]}</span>
              {status.progress.total > 0 && (
                <span>
                  {status.progress.done} из {status.progress.total}
                </span>
              )}
            </div>
            {processing && (
              <div className="progress">
                <div className="progress__bar" style={{ width: `${progress}%` }} />
              </div>
            )}
          </div>
        )}

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}

        {report && (
          <div className="report">
            <h2>{report.region_title}</h2>
            <dl className="report__grid">
              <div>
                <dt>Заявок</dt>
                <dd>{report.requests}</dd>
              </div>
              <div>
                <dt>Инженеров</dt>
                <dd>{report.engineers}</dd>
              </div>
              <div>
                <dt>Расстояния</dt>
                <dd>{MATRIX_SOURCE_LABELS[report.matrix_source]}</dd>
              </div>
              <div>
                <dt>Источник</dt>
                <dd>{report.source === 'bundle' ? 'Готовый набор JSON' : 'Выгрузка Билайна CSV'}</dd>
              </div>
            </dl>
            <p className="report__geo">
              Адреса на карте: {PRECISIONS.map((key) => `${PRECISION_LABELS[key]} ${report.geocoding[key] ?? 0}`).join(' · ')}
            </p>
            {report.not_found.length > 0 && (
              <details>
                <summary>Не найдены на карте: {report.not_found.length}</summary>
                <ul>
                  {report.not_found.map((item) => (
                    <li key={item.request_id}>
                      {item.request_id}: {item.address}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {report.skipped_rows.length > 0 && (
              <details>
                <summary>Пропущено строк: {report.skipped_rows.length}</summary>
                <ul>
                  {report.skipped_rows.map((row) => (
                    <li key={row}>{row}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}

        {status?.status === 'ready' && (
          <button type="button" className="btn btn-primary btn-large" onClick={() => void plan()} disabled={busy}>
            {busy ? 'Считаем план…' : 'Спланировать'}
          </button>
        )}
      </section>
    </main>
  );
}
```

- [ ] **Step 4: Прогнать тест**

Run: `npx vitest run src/components/UploadScreen.test.tsx`
Expected: `Tests  3 passed (3)`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/UploadScreen.tsx frontend/src/components/UploadScreen.test.tsx
git commit -m "feat(frontend): экран загрузки с прогрессом и отчётом предподсчёта" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```

---

## Task 7: Карточка объяснения по заявке

**Files:**
- Create: `frontend/src/components/ExplanationCard.tsx`
- Test: `frontend/src/components/ExplanationCard.test.tsx`

**Interfaces:**
- Consumes: `getExplanation`, `useAppStore` (`datasetId`, `state`, `selectedRequestId`, `selectRequest`, `showPrevious`), подписи и форматтеры.
- Produces: компонент `ExplanationCard()` без пропсов. Перезапрашивает объяснение при смене заявки и при смене `state.version`. Ничего не рендерит без выбранной заявки.

- [ ] **Step 1: Написать падающий тест**

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, getExplanation: vi.fn() };
});

import * as api from '../api/client';
import { useAppStore } from '../store/useAppStore';
import { makeExplanation, makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { ExplanationCard } from './ExplanationCard';

beforeEach(() => {
  vi.resetAllMocks();
  resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedRequestId: '50104' });
});

describe('ExplanationCard', () => {
  it('loads and shows the explanation for the selected request', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    render(<ExplanationCard />);
    expect(await screen.findByText(/Назначена Бригада Арташкин/)).toBeInTheDocument();
    expect(api.getExplanation).toHaveBeenCalledWith('d_test', '50104');
    expect(screen.getByText('Временное окно')).toBeInTheDocument();
    expect(screen.getByText('Не нужен дополнительный инженер')).toBeInTheDocument();
    expect(screen.getByText('Бригада Белузин').closest('tr')).toHaveTextContent('+2,1 км');
    expect(screen.getByText(/окно 14:00–16:00 · 45 мин · Локальные работы/)).toBeInTheDocument();
  });

  it('shows the error and closes', async () => {
    vi.mocked(api.getExplanation).mockRejectedValue(new api.ApiError(404, 'Заявка не найдена'));
    render(<ExplanationCard />);
    expect(await screen.findByText('Заявка не найдена')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть объяснение' }));
    expect(useAppStore.getState().selectedRequestId).toBeNull();
  });

  it('renders nothing without a selection', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    const { container } = render(<ExplanationCard />);
    expect(container).toBeEmptyDOMElement();
    expect(api.getExplanation).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `npx vitest run src/components/ExplanationCard.test.tsx`
Expected: FAIL с `Failed to resolve import "./ExplanationCard"`.

- [ ] **Step 3: Реализовать `frontend/src/components/ExplanationCard.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { getExplanation } from '../api/client';
import type { Explanation } from '../api/types';
import { formatKm, formatSigned, formatWindow, shortAddress, SKILL_LABELS, TRANSPORT_LABELS } from '../lib/format';
import { byId } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';

export function ExplanationCard() {
  const datasetId = useAppStore((s) => s.datasetId);
  const state = useAppStore((s) => s.state);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const version = state?.version ?? 0;
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!datasetId || !selectedRequestId) {
      setExplanation(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getExplanation(datasetId, selectedRequestId)
      .then((data) => {
        if (!cancelled) setExplanation(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setExplanation(null);
        setError(err instanceof Error ? err.message : 'Не удалось получить объяснение');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId, selectedRequestId, version]);

  if (!state || !selectedRequestId) return null;
  const request = state.requests.find((item) => item.id === selectedRequestId);
  const engineers = byId(state.engineers);
  const nameOf = (engineerId: string | null) => (engineerId ? (engineers.get(engineerId)?.name ?? engineerId) : '—');

  return (
    <section className="explanation" aria-label="Объяснение по заявке">
      <header className="explanation__head">
        <div>
          <h3>Заявка {selectedRequestId}</h3>
          {request && (
            <p className="muted">
              {shortAddress(request.address)} · окно {formatWindow(request.window_start, request.window_end)} ·{' '}
              {request.duration_min} мин · {SKILL_LABELS[request.skill]}
              {request.transport_required ? ` · нужен транспорт «${TRANSPORT_LABELS[request.transport_required]}»` : ''}
              {request.priority === 'urgent' ? ' · срочная' : ''}
            </p>
          )}
        </div>
        <button type="button" className="btn btn-ghost btn-small" onClick={() => selectRequest(null)} aria-label="Закрыть объяснение">
          ✕
        </button>
      </header>
      {showPrevious && <p className="note">Объяснение относится к текущему плану, после события.</p>}
      {loading && <p className="muted">Загружаем объяснение…</p>}
      {error && <p className="error-text">{error}</p>}
      {explanation && !loading && (
        <>
          <p className="explanation__summary">{explanation.summary}</p>
          {explanation.visit && (
            <p className="muted">
              {nameOf(explanation.engineer_id)}: приезд {explanation.visit.arrival}, начало {explanation.visit.start}, окончание{' '}
              {explanation.visit.end}, в пути {explanation.visit.leg_min} мин ({formatKm(explanation.visit.leg_km)})
            </p>
          )}
          {explanation.unassigned && <p className="warn-text">{explanation.unassigned.reason_text}</p>}
          <ul className="checks">
            {explanation.constraints.map((check) => (
              <li key={check.name} className={check.ok ? 'check check--ok' : 'check check--fail'}>
                <span aria-hidden="true">{check.ok ? '✓' : '✗'}</span>
                <strong>{check.name}</strong>
                <span>{check.detail}</span>
              </li>
            ))}
          </ul>
          {explanation.factors.length > 0 && (
            <>
              <h4>Почему такой выбор</h4>
              <ul className="factors">
                {explanation.factors.map((factor) => (
                  <li key={factor}>{factor}</li>
                ))}
              </ul>
            </>
          )}
          {explanation.alternatives.length > 0 && (
            <>
              <h4>Другие инженеры</h4>
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Инженер</th>
                    <th>Может взять</th>
                    <th>Начало</th>
                    <th>Доп. пробег</th>
                    <th>Комментарий</th>
                  </tr>
                </thead>
                <tbody>
                  {explanation.alternatives.map((alternative) => (
                    <tr key={alternative.engineer_id}>
                      <td>{nameOf(alternative.engineer_id)}</td>
                      <td>{alternative.feasible ? 'да' : 'нет'}</td>
                      <td>{alternative.start ?? '—'}</td>
                      <td>{alternative.extra_km === null ? '—' : `${formatSigned(alternative.extra_km, 1)} км`}</td>
                      <td>{alternative.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Прогнать тест**

Run: `npx vitest run src/components/ExplanationCard.test.tsx`
Expected: `Tests  3 passed (3)`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ExplanationCard.tsx frontend/src/components/ExplanationCard.test.tsx
git commit -m "feat(frontend): карточка объяснения выбора инженера" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```

---

## Task 8: Правая панель и вкладки

**Files:**
- Create: `frontend/src/components/panel/tabs.ts`, `frontend/src/components/panel/RightPanel.tsx`, `frontend/src/components/panel/RequestsTab.tsx`, `frontend/src/components/panel/UnassignedTab.tsx`, `frontend/src/components/panel/ComparisonTab.tsx`, `frontend/src/components/panel/TimelineTab.tsx`
- Test: `frontend/src/components/panel/RequestsTab.test.tsx`, `frontend/src/components/panel/OtherTabs.test.tsx`

**Interfaces:**
- Consumes: `ExplanationCard`, функции `planView.ts`, `timeline.ts`, `comparison.ts`, `events.ts`, `engineerColor`, стор.
- Produces: `interface PanelTab {id: string; title: string; component: ComponentType; badge?: (app: AppState) => number | null}`, `PANEL_TABS: PanelTab[]` с id `requests`, `timeline`, `unassigned`, `comparison`; компоненты `RightPanel`, `RequestsTab`, `UnassignedTab`, `ComparisonTab`, `TimelineTab` без пропсов. Кнопки «Отменить» и «Вернуть» отправляют событие со временем `max(eventTime, state.now)`; отмена начатой (закреплённой) работы заблокирована.

- [ ] **Step 1: Написать падающие тесты**

`frontend/src/components/panel/RequestsTab.test.tsx`:

```tsx
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { RequestsTab } from './RequestsTab';

const rowOf = (requestId: string) => screen.getByText(requestId).closest('li') as HTMLElement;

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '13:30' });
});

describe('RequestsTab', () => {
  it('lists requests with engineer, planned start and badges', () => {
    render(<RequestsTab />);
    const moved = rowOf('50104');
    expect(within(moved).getByText('Начало 14:00')).toBeInTheDocument();
    expect(within(moved).getByText('Бригада Арташкин')).toBeInTheDocument();
    expect(within(moved).getByText('Перенесена')).toBeInTheDocument();

    const urgent = rowOf('URG-001');
    expect(within(urgent).getByText('Срочная')).toBeInTheDocument();
    expect(within(urgent).getByText('Новое назначение')).toBeInTheDocument();

    expect(within(rowOf('18754')).getByText('Не назначена')).toBeInTheDocument();
    expect(within(rowOf('10135')).getByRole('button', { name: 'Вернуть' })).toBeEnabled();
  });

  it('cancels a request at the chosen event time without selecting the row', () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ applyEvent });
    render(<RequestsTab />);
    fireEvent.click(within(rowOf('50104')).getByRole('button', { name: 'Отменить' }));
    expect(applyEvent).toHaveBeenCalledWith({ type: 'cancel', time: '13:30', request: null, request_id: '50104', engineer_id: null });
    expect(useAppStore.getState().selectedRequestId).toBeNull();
  });

  it('selects a request on row click', () => {
    render(<RequestsTab />);
    fireEvent.click(rowOf('46393'));
    expect(useAppStore.getState().selectedRequestId).toBe('46393');
  });

  it('filters by engineer in route order and blocks cancelling started work', () => {
    render(<RequestsTab />);
    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E01' } });
    const titles = screen.getAllByRole('listitem').map((item) => item.querySelector('strong')?.textContent);
    expect(titles).toEqual(['74198', '86160', '50104', '46393']);
    expect(within(rowOf('74198')).getByRole('button', { name: 'Отменить' })).toBeDisabled();
    expect(useAppStore.getState().selectedEngineerId).toBe('E01');
  });

  it('shows the previous plan without diff badges and with actions disabled', () => {
    useAppStore.setState({ showPrevious: true });
    render(<RequestsTab />);
    expect(within(rowOf('50104')).getByText('Бригада Белузин')).toBeInTheDocument();
    expect(screen.queryByText('Перенесена')).not.toBeInTheDocument();
    expect(within(rowOf('46393')).getByRole('button', { name: 'Отменить' })).toBeDisabled();
  });
});
```

`frontend/src/components/panel/OtherTabs.test.tsx`:

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { ComparisonTab } from './ComparisonTab';
import { PANEL_TABS } from './tabs';
import { TimelineTab } from './TimelineTab';
import { UnassignedTab } from './UnassignedTab';

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('panel tabs', () => {
  it('registers the four base tabs in order with an unassigned badge', () => {
    expect(PANEL_TABS.map((tab) => tab.id)).toEqual(['requests', 'timeline', 'unassigned', 'comparison']);
    const unassigned = PANEL_TABS.find((tab) => tab.id === 'unassigned');
    expect(unassigned?.badge?.(useAppStore.getState())).toBe(1);
  });

  it('UnassignedTab shows the reason and selects the request', () => {
    render(<UnassignedTab />);
    expect(screen.getByText('Не помещается в окно или смену')).toBeInTheDocument();
    expect(screen.getByText(/даже без других заявок Бригада Белузин/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('18754'));
    expect(useAppStore.getState().selectedRequestId).toBe('18754');
  });

  it('UnassignedTab shows an empty message when everything is assigned', () => {
    const state = makePlanningState();
    resetStore({ state: { ...state, plan: { ...state.plan, unassigned: [] } } });
    render(<UnassignedTab />);
    expect(screen.getByText('Все заявки распределены.')).toBeInTheDocument();
  });

  it('ComparisonTab shows three plans and the delta to baseline', () => {
    render(<ComparisonTab />);
    const engineers = screen.getByText('Задействовано инженеров').closest('tr') as HTMLElement;
    expect(engineers).toHaveTextContent('Задействовано инженеров223');
    expect(screen.getByText('−1')).toHaveClass('delta--better');
    expect(screen.getByText('Бригада Комарь').closest('tr')).toHaveTextContent('9,8 км');
  });

  it('TimelineTab renders visit bars and selects a request', () => {
    render(<TimelineTab />);
    fireEvent.click(screen.getByRole('button', { name: 'Заявка 50104 14:00–14:45' }));
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
    expect(screen.getByText(/текущее время 13:00/)).toBeInTheDocument();
    expect(screen.getByText('09:00')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `npx vitest run src/components/panel`
Expected: FAIL с `Failed to resolve import "./RequestsTab"` и `Failed to resolve import "./ComparisonTab"`.

- [ ] **Step 3: Реализовать `frontend/src/components/panel/RequestsTab.tsx`**

```tsx
import type { ServiceRequest } from '../../api/types';
import { engineerColor } from '../../lib/colors';
import { cancelEvent, restoreEvent } from '../../lib/events';
import { formatWindow, isValidTime, laterTime, shortAddress } from '../../lib/format';
import {
  assignmentIndex,
  byId,
  DIFF_MARK_LABELS,
  diffMarks,
  displayedPlan,
  engineerIdsOf,
  routeRequestIds,
  sortRequestsForList,
} from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';

export function RequestsTab() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const selectEngineer = useAppStore((s) => s.selectEngineer);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const eventTime = useAppStore((s) => s.eventTime);
  const busy = useAppStore((s) => s.busy);
  if (!state) return null;

  const plan = displayedPlan(state, showPrevious);
  const assignments = assignmentIndex(plan);
  const engineers = byId(state.engineers);
  const requests = byId(state.requests);
  const ids = engineerIdsOf(state);
  const marks = showPrevious ? new Map() : diffMarks(state.last_diff);
  const time = isValidTime(eventTime) ? laterTime(eventTime, state.now) : state.now;
  const rows = selectedEngineerId
    ? routeRequestIds(plan, selectedEngineerId)
        .map((id) => requests.get(id))
        .filter((request): request is ServiceRequest => request !== undefined)
    : sortRequestsForList(state.requests, plan);

  return (
    <div className="requests-tab">
      <div className="tab-toolbar">
        <label className="field field--inline">
          <span>Инженер</span>
          <select value={selectedEngineerId ?? ''} onChange={(event) => selectEngineer(event.target.value || null)}>
            <option value="">Все инженеры</option>
            {state.engineers.map((engineer) => (
              <option key={engineer.id} value={engineer.id}>
                {engineer.name}
                {engineer.available ? '' : ' (недоступен)'}
              </option>
            ))}
          </select>
        </label>
        <span className="muted">Заявок: {rows.length}</span>
      </div>
      <ul className="request-list">
        {rows.map((request) => {
          const info = assignments.get(request.id);
          const engineer = info ? engineers.get(info.engineerId) : undefined;
          const mark = marks.get(request.id);
          const cancelled = request.status === 'cancelled';
          const pinned = Boolean(info?.visit.pinned);
          const classes = [
            'request-row',
            request.id === selectedRequestId ? 'request-row--selected' : '',
            cancelled ? 'request-row--cancelled' : '',
            mark ? 'request-row--changed' : '',
          ]
            .filter(Boolean)
            .join(' ');
          return (
            <li key={request.id} className={classes} onClick={() => selectRequest(request.id)}>
              <span className="dot" style={{ background: engineerColor(info?.engineerId, ids) }} />
              <div className="request-row__main">
                <div className="request-row__title">
                  <strong>{request.id}</strong>
                  <span>{shortAddress(request.address)}</span>
                </div>
                <div className="request-row__meta">
                  <span>Окно {formatWindow(request.window_start, request.window_end)}</span>
                  <span>{info ? `Начало ${info.visit.start}` : cancelled ? 'Отменена' : 'Не назначена'}</span>
                  <span>{engineer ? engineer.name : '—'}</span>
                </div>
                <div className="badges">
                  {request.priority === 'urgent' && <span className="badge badge--urgent">Срочная</span>}
                  {cancelled && <span className="badge badge--cancelled">Отменена</span>}
                  {pinned && <span className="badge">Закреплена</span>}
                  {mark && <span className="badge badge--diff">{DIFF_MARK_LABELS[mark as keyof typeof DIFF_MARK_LABELS]}</span>}
                </div>
              </div>
              <button
                type="button"
                className="btn btn-small"
                disabled={busy || showPrevious || (pinned && !cancelled)}
                title={pinned && !cancelled ? 'Работа уже началась, отменить нельзя' : undefined}
                onClick={(event) => {
                  event.stopPropagation();
                  void applyEvent(cancelled ? restoreEvent(request.id, time) : cancelEvent(request.id, time));
                }}
              >
                {cancelled ? 'Вернуть' : 'Отменить'}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: Реализовать `frontend/src/components/panel/TimelineTab.tsx`**

```tsx
import { Fragment } from 'react';
import { engineerColor } from '../../lib/colors';
import { fromMinutes, toMinutes } from '../../lib/format';
import { displayedPlan, engineerIdsOf } from '../../lib/planView';
import { hourTicks, percent, timelineRows, timeScale } from '../../lib/timeline';
import { useAppStore } from '../../store/useAppStore';

export function TimelineTab() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  if (!state) return null;

  const plan = displayedPlan(state, showPrevious);
  const scale = timeScale(state, plan);
  const rows = timelineRows(state, plan, scale);
  const ids = engineerIdsOf(state);
  const nowLeft = percent(scale, toMinutes(state.now));

  return (
    <div className="timeline">
      <div className="timeline__row timeline__row--header">
        <div className="timeline__label" />
        <div className="timeline__track">
          {hourTicks(scale).map((tick) => (
            <span key={tick} className="timeline__tick" style={{ left: `${percent(scale, tick)}%` }}>
              {fromMinutes(tick)}
            </span>
          ))}
        </div>
      </div>
      {rows.map((row) => {
        const color = engineerColor(row.engineer.id, ids);
        return (
          <div key={row.engineer.id} className="timeline__row">
            <div className="timeline__label">
              <span className="dot" style={{ background: color }} />
              {row.engineer.name}
            </div>
            <div className="timeline__track">
              <div className="timeline__shift" style={{ left: `${row.shiftLeft}%`, width: `${row.shiftWidth}%` }} />
              {row.unavailableLeft !== null && (
                <div className="timeline__unavailable" style={{ left: `${row.unavailableLeft}%` }} title="Инженер недоступен" />
              )}
              {row.bars.map((bar) => (
                <Fragment key={bar.requestId}>
                  <div
                    className="timeline__window"
                    style={{ left: `${bar.windowLeft}%`, width: `${bar.windowWidth}%`, borderColor: color }}
                  />
                  <button
                    type="button"
                    className={[
                      'timeline__bar',
                      bar.pinned ? 'timeline__bar--pinned' : '',
                      bar.urgent ? 'timeline__bar--urgent' : '',
                      bar.late ? 'timeline__bar--late' : '',
                      bar.clipped ? 'timeline__bar--clipped' : '',
                      bar.requestId === selectedRequestId ? 'timeline__bar--selected' : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                    style={{ left: `${bar.left}%`, width: `${bar.width}%`, background: color }}
                    title={`${bar.requestId}: ${bar.label}${bar.clipped ? ' (выходит за шкалу)' : ''}`}
                    aria-label={`Заявка ${bar.requestId} ${bar.label}`}
                    onClick={() => selectRequest(bar.requestId)}
                  />
                </Fragment>
              ))}
              {nowLeft > 0 && <div className="timeline__now" style={{ left: `${nowLeft}%` }} />}
            </div>
          </div>
        );
      })}
      <p className="muted timeline__legend">
        Полоса: работа по плану. Рамка: окно визита. Штриховка: работа уже началась или инженер недоступен. Красная линия:
        текущее время {state.now}.
      </p>
    </div>
  );
}
```

- [ ] **Step 5: Реализовать `frontend/src/components/panel/UnassignedTab.tsx`**

```tsx
import { formatWindow, REASON_LABELS, shortAddress, SKILL_LABELS } from '../../lib/format';
import { byId, displayedPlan } from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';

export function UnassignedTab() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectRequest = useAppStore((s) => s.selectRequest);
  if (!state) return null;

  const plan = displayedPlan(state, showPrevious);
  const requests = byId(state.requests);
  if (plan.unassigned.length === 0) return <p className="empty">Все заявки распределены.</p>;

  return (
    <ul className="unassigned-list">
      {plan.unassigned.map((item) => {
        const request = requests.get(item.request_id);
        return (
          <li key={item.request_id} className="unassigned-item" onClick={() => selectRequest(item.request_id)}>
            <div className="unassigned-item__head">
              <strong>{item.request_id}</strong>
              <span className="badge badge--warn">{REASON_LABELS[item.reason_code]}</span>
            </div>
            {request && (
              <div className="muted">
                {shortAddress(request.address)} · окно {formatWindow(request.window_start, request.window_end)} ·{' '}
                {SKILL_LABELS[request.skill]}
              </div>
            )}
            <p>{item.reason_text}</p>
          </li>
        );
      })}
    </ul>
  );
}
```

- [ ] **Step 6: Реализовать `frontend/src/components/panel/ComparisonTab.tsx`**

```tsx
import { kmPerEngineerRows, metricRows } from '../../lib/comparison';
import { formatKm } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

const kmCell = (value: number | null) => (value === null ? '—' : formatKm(value));

export function ComparisonTab() {
  const state = useAppStore((s) => s.state);
  if (!state) return null;
  const rows = metricRows(state);
  const km = kmPerEngineerRows(state);

  return (
    <div className="comparison">
      <p className="muted">
        Базовый вариант по ТЗ (п. 2.3): заявки по порядку поступления первому подходящему инженеру, без оптимизации.
        Диспетчеры: фактическое распределение из контрольного файла, пересчитанное нашей моделью времени и пробега.
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>Показатель</th>
            <th>Базовый (FCFS)</th>
            <th>Оптимизированный</th>
            <th>Диспетчеры</th>
            <th>Оптимизированный к базовому</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <td>{row.label}</td>
              <td>{row.baseline}</td>
              <td>
                <strong>{row.optimized}</strong>
              </td>
              <td>{row.dispatchers}</td>
              <td className={`delta delta--${row.verdict}`}>{row.delta}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h4>Пробег по инженерам</h4>
      <table className="table">
        <thead>
          <tr>
            <th>Инженер</th>
            <th>Базовый</th>
            <th>Оптимизированный</th>
            <th>Диспетчеры</th>
          </tr>
        </thead>
        <tbody>
          {km.map((row) => (
            <tr key={row.engineerId}>
              <td>{row.name}</td>
              <td>{kmCell(row.baseline)}</td>
              <td>{kmCell(row.optimized)}</td>
              <td>{kmCell(row.dispatchers)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 7: Реализовать реестр вкладок `frontend/src/components/panel/tabs.ts`**

```ts
import type { ComponentType } from 'react';
import { displayedPlan } from '../../lib/planView';
import type { AppState } from '../../store/useAppStore';
import { ComparisonTab } from './ComparisonTab';
import { RequestsTab } from './RequestsTab';
import { TimelineTab } from './TimelineTab';
import { UnassignedTab } from './UnassignedTab';

export interface PanelTab {
  id: string;
  title: string;
  component: ComponentType;
  /** Число в бейдже вкладки; null скрывает бейдж */
  badge?: (app: AppState) => number | null;
}

export const PANEL_TABS: PanelTab[] = [
  { id: 'requests', title: 'Заявки', component: RequestsTab },
  { id: 'timeline', title: 'Таймлайн', component: TimelineTab },
  {
    id: 'unassigned',
    title: 'Неназначенные',
    component: UnassignedTab,
    badge: (app) => (app.state ? displayedPlan(app.state, app.showPrevious).unassigned.length || null : null),
  },
  { id: 'comparison', title: 'Сравнение', component: ComparisonTab },
];
```

- [ ] **Step 8: Реализовать `frontend/src/components/panel/RightPanel.tsx`**

```tsx
import { useAppStore } from '../../store/useAppStore';
import { ExplanationCard } from '../ExplanationCard';
import { PANEL_TABS } from './tabs';

export function RightPanel() {
  const app = useAppStore();
  const tab = PANEL_TABS.find((item) => item.id === app.activeTab) ?? PANEL_TABS[0];
  const Active = tab.component;
  return (
    <aside className="panel">
      <ExplanationCard />
      <nav className="tabs" role="tablist">
        {PANEL_TABS.map((item) => {
          const badge = item.badge?.(app) ?? null;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={item.id === tab.id}
              className={`tabs__tab${item.id === tab.id ? ' tabs__tab--active' : ''}`}
              onClick={() => app.setTab(item.id)}
            >
              {item.title}
              {badge ? <span className="tabs__badge">{badge}</span> : null}
            </button>
          );
        })}
      </nav>
      <div className="panel__content" role="tabpanel">
        <Active />
      </div>
    </aside>
  );
}
```

- [ ] **Step 9: Прогнать тесты и проверку типов**

Run: `npx vitest run src/components/panel && npx tsc --noEmit`
Expected: `Test Files  2 passed (2)`, `Tests  10 passed (10)`, `tsc` без вывода.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/panel
git commit -m "feat(frontend): правая панель со списком заявок, таймлайном, неназначенными и сравнением" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```

---

## Task 9: События дня и баннер изменений

**Files:**
- Create: `frontend/src/components/events/EventToolbar.tsx`, `frontend/src/components/events/UrgentRequestDialog.tsx`, `frontend/src/components/events/EngineerUnavailableDialog.tsx`, `frontend/src/components/DiffBanner.tsx`
- Test: `frontend/src/components/events/Dialogs.test.tsx`, `frontend/src/components/DiffBanner.test.tsx`

**Interfaces:**
- Consumes: `buildUrgentEvent`, `validateUrgentForm`, `newUrgentId`, `timeError`, `unavailableEvent`, `describeEvent`, стор (`applyEvent`, `eventTime`, `setEventTime`, `pickMode`, `pickedPoint`, `startPick`, `finishPick`, `setShowPrevious`).
- Produces: `EventToolbar()` (поле «Время события», кнопки «Срочная заявка» и «Инженер недоступен»), `UrgentRequestDialog({onClose})`, `EngineerUnavailableDialog({onClose})`, `DiffBanner()`. Диалоги немодальные: пока открыт диалог срочной заявки, можно кликнуть по карте, и `MapContent` из Задачи 10 запишет `pickedPoint`.

- [ ] **Step 1: Написать падающие тесты**

`frontend/src/components/events/Dialogs.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { EngineerUnavailableDialog } from './EngineerUnavailableDialog';
import { EventToolbar } from './EventToolbar';
import { UrgentRequestDialog } from './UrgentRequestDialog';

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState(), eventTime: '13:00' });
});

describe('UrgentRequestDialog', () => {
  it('validates the form and submits an urgent request', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    useAppStore.setState({ applyEvent });
    render(<UrgentRequestDialog onClose={onClose} />);
    const submit = screen.getByRole('button', { name: 'Добавить и перепланировать' });

    fireEvent.click(submit);
    expect(await screen.findByText('Укажите адрес или точку на карте')).toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Адрес'), { target: { value: 'Город Москва, ул.Ташкентская, д. 16к2' } });
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '12:00' } });
    fireEvent.click(submit);
    expect(await screen.findByText('Время события не может быть раньше 13:00')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '13:15' } });
    fireEvent.click(submit);
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    const event = applyEvent.mock.calls[0][0];
    expect(event).toMatchObject({ type: 'urgent', time: '13:15', request_id: null });
    expect(event.request).toMatchObject({
      address: 'Город Москва, ул.Ташкентская, д. 16к2',
      window_start: '13:00',
      window_end: '15:00',
      duration_min: 60,
      skill: 'emergency',
      transport_required: 'car',
      priority: 'urgent',
    });
  });

  it('uses a point picked on the map and starts picking mode', () => {
    useAppStore.setState({ pickedPoint: { lat: 55.71234, lon: 37.80123 } });
    render(<UrgentRequestDialog onClose={() => undefined} />);
    expect(screen.getByText('Точка: 55.71234, 37.80123')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Указать точку на карте' }));
    expect(useAppStore.getState().pickMode).toBe(true);
    expect(screen.getByRole('button', { name: 'Кликните по карте…' })).toBeDisabled();
  });
});

describe('EngineerUnavailableDialog', () => {
  it('offers only available engineers and submits the event', async () => {
    const applyEvent = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    useAppStore.setState({ applyEvent, eventTime: '14:00' });
    render(<EngineerUnavailableDialog onClose={onClose} />);
    const options = screen.getAllByRole('option').map((option) => option.textContent);
    expect(options).toEqual(['Бригада Арташкин', 'Бригада Белузин']);
    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E02' } });
    fireEvent.click(screen.getByRole('button', { name: 'Перепланировать' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(applyEvent).toHaveBeenCalledWith({
      type: 'engineer_unavailable',
      time: '14:00',
      request: null,
      request_id: null,
      engineer_id: 'E02',
    });
  });
});

describe('EventToolbar', () => {
  it('rejects an event time earlier than now and opens dialogs', () => {
    render(<EventToolbar />);
    fireEvent.change(screen.getByLabelText('Время события'), { target: { value: '12:00' } });
    expect(screen.getByText('Время события не может быть раньше 13:00')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Инженер недоступен' }));
    expect(screen.getByRole('dialog', { name: 'Инженер недоступен' })).toBeInTheDocument();
  });
});
```

`frontend/src/components/DiffBanner.test.tsx`:

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../store/useAppStore';
import { makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { DiffBanner } from './DiffBanner';

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('DiffBanner', () => {
  it('summarises the last event and metrics before and after', () => {
    render(<DiffBanner />);
    expect(screen.getByText('Срочная заявка URG-001 в 13:00')).toBeInTheDocument();
    expect(screen.getByText(/Новых назначений: 1 · перенесено: 1 · снято: 0 · сдвиг\s+времени: 1/)).toBeInTheDocument();
    expect(screen.getByText(/пробег 31,7 км → 34,9 км · не назначено 1 → 1/)).toBeInTheDocument();
  });

  it('switches between the plan before and after the event', () => {
    render(<DiffBanner />);
    fireEvent.click(screen.getByRole('button', { name: 'До события' }));
    expect(useAppStore.getState().showPrevious).toBe(true);
    expect(screen.getByRole('button', { name: 'До события' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Скрыть' }));
    expect(useAppStore.getState().showPrevious).toBe(false);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('is hidden when there is no diff', () => {
    resetStore({ state: makePlanningState({ last_diff: null }) });
    const { container } = render(<DiffBanner />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `npx vitest run src/components/events src/components/DiffBanner.test.tsx`
Expected: FAIL с `Failed to resolve import "./EngineerUnavailableDialog"` и `Failed to resolve import "./DiffBanner"`.

- [ ] **Step 3: Реализовать `frontend/src/components/events/UrgentRequestDialog.tsx`**

`noValidate` обязателен: у поля времени есть `min`, и без него браузер (и jsdom) молча не отправит форму, а русская ошибка не появится.

```tsx
import { useState, type FormEvent } from 'react';
import type { Skill, Transport } from '../../api/types';
import { buildUrgentEvent, newUrgentId, validateUrgentForm, type UrgentForm } from '../../lib/events';
import { addMinutes, isValidTime, laterTime, SKILL_LABELS, TRANSPORT_LABELS } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

const SKILLS: Skill[] = ['emergency', 'connection', 'local'];
const TRANSPORTS: Transport[] = ['car', 'foot', 'bike', 'public'];

export function UrgentRequestDialog({ onClose }: { onClose: () => void }) {
  const state = useAppStore((s) => s.state);
  const eventTime = useAppStore((s) => s.eventTime);
  const busy = useAppStore((s) => s.busy);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const pickMode = useAppStore((s) => s.pickMode);
  const pickedPoint = useAppStore((s) => s.pickedPoint);
  const startPick = useAppStore((s) => s.startPick);
  const finishPick = useAppStore((s) => s.finishPick);
  const now = state?.now ?? '00:00';
  const initialTime = isValidTime(eventTime) ? laterTime(eventTime, now) : now;
  const [form, setForm] = useState<UrgentForm>(() => ({
    address: '',
    point: null,
    windowStart: initialTime,
    windowEnd: addMinutes(initialTime, 120),
    durationMin: 60,
    skill: 'emergency',
    transport: 'car',
    time: initialTime,
  }));
  const [errors, setErrors] = useState<string[]>([]);
  const point = pickedPoint ?? form.point;

  const update = <K extends keyof UrgentForm>(key: K, value: UrgentForm[K]) => setForm((prev) => ({ ...prev, [key]: value }));

  const close = () => {
    finishPick(null);
    onClose();
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const candidate = { ...form, point };
    const found = validateUrgentForm(candidate, now);
    setErrors(found);
    if (found.length > 0) return;
    const ok = await applyEvent(buildUrgentEvent(candidate, newUrgentId(Date.now())));
    if (ok) close();
  };

  return (
    <div className="dialog" role="dialog" aria-label="Срочная заявка">
      <form noValidate onSubmit={(event) => void submit(event)}>
        <h3>Срочная заявка</h3>
        <label className="field">
          <span>Адрес</span>
          <input value={form.address} onChange={(event) => update('address', event.target.value)} placeholder="Город Москва, ул.Ташкентская, д. 16к2" />
        </label>
        <div className="field-row">
          <button type="button" className="btn btn-small" onClick={startPick} disabled={pickMode}>
            {pickMode ? 'Кликните по карте…' : 'Указать точку на карте'}
          </button>
          {point && (
            <span className="muted">
              Точка: {point.lat.toFixed(5)}, {point.lon.toFixed(5)}
            </span>
          )}
        </div>
        <div className="field-row">
          <label className="field">
            <span>Окно с</span>
            <input type="time" value={form.windowStart} onChange={(event) => update('windowStart', event.target.value)} />
          </label>
          <label className="field">
            <span>Окно до</span>
            <input type="time" value={form.windowEnd} onChange={(event) => update('windowEnd', event.target.value)} />
          </label>
          <label className="field">
            <span>Длительность, мин</span>
            <input type="number" min={5} step={5} value={form.durationMin} onChange={(event) => update('durationMin', Number(event.target.value))} />
          </label>
        </div>
        <div className="field-row">
          <label className="field">
            <span>Навык</span>
            <select value={form.skill} onChange={(event) => update('skill', event.target.value as Skill)}>
              {SKILLS.map((skill) => (
                <option key={skill} value={skill}>
                  {SKILL_LABELS[skill]}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Транспорт</span>
            <select value={form.transport} onChange={(event) => update('transport', event.target.value as Transport | '')}>
              <option value="">Не требуется</option>
              {TRANSPORTS.map((transport) => (
                <option key={transport} value={transport}>
                  {TRANSPORT_LABELS[transport]}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="field">
          <span>Время события</span>
          <input type="time" value={form.time} min={now} onChange={(event) => update('time', event.target.value)} />
        </label>
        {errors.length > 0 && (
          <ul className="error-list" role="alert">
            {errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        )}
        <div className="dialog__actions">
          <button type="button" className="btn btn-ghost" onClick={close}>
            Отмена
          </button>
          <button type="submit" className="btn btn-danger" disabled={busy}>
            Добавить и перепланировать
          </button>
        </div>
      </form>
    </div>
  );
}
```

- [ ] **Step 4: Реализовать `frontend/src/components/events/EngineerUnavailableDialog.tsx`**

```tsx
import { useState, type FormEvent } from 'react';
import { timeError, unavailableEvent } from '../../lib/events';
import { isValidTime, laterTime } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

export function EngineerUnavailableDialog({ onClose }: { onClose: () => void }) {
  const state = useAppStore((s) => s.state);
  const eventTime = useAppStore((s) => s.eventTime);
  const busy = useAppStore((s) => s.busy);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const now = state?.now ?? '00:00';
  const engineers = (state?.engineers ?? []).filter((engineer) => engineer.available);
  const [engineerId, setEngineerId] = useState(engineers[0]?.id ?? '');
  const [time, setTime] = useState(isValidTime(eventTime) ? laterTime(eventTime, now) : now);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const problem = engineerId ? timeError(time, now) : 'Выберите инженера';
    setError(problem);
    if (problem) return;
    if (await applyEvent(unavailableEvent(engineerId, time))) onClose();
  };

  return (
    <div className="dialog" role="dialog" aria-label="Инженер недоступен">
      <form noValidate onSubmit={(event) => void submit(event)}>
        <h3>Инженер недоступен</h3>
        <label className="field">
          <span>Инженер</span>
          <select value={engineerId} onChange={(event) => setEngineerId(event.target.value)}>
            {engineers.map((engineer) => (
              <option key={engineer.id} value={engineer.id}>
                {engineer.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Недоступен с</span>
          <input type="time" value={time} min={now} onChange={(event) => setTime(event.target.value)} />
        </label>
        <p className="muted">Начатые до этого времени работы останутся за инженером, остальные будут перераспределены.</p>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className="dialog__actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Перепланировать
          </button>
        </div>
      </form>
    </div>
  );
}
```

- [ ] **Step 5: Реализовать `frontend/src/components/events/EventToolbar.tsx`**

```tsx
import { useState } from 'react';
import { timeError } from '../../lib/events';
import { useAppStore } from '../../store/useAppStore';
import { EngineerUnavailableDialog } from './EngineerUnavailableDialog';
import { UrgentRequestDialog } from './UrgentRequestDialog';

export function EventToolbar() {
  const state = useAppStore((s) => s.state);
  const eventTime = useAppStore((s) => s.eventTime);
  const setEventTime = useAppStore((s) => s.setEventTime);
  const busy = useAppStore((s) => s.busy);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const [dialog, setDialog] = useState<'urgent' | 'unavailable' | null>(null);
  if (!state) return null;

  const error = timeError(eventTime, state.now);
  return (
    <div className="event-toolbar">
      <label className="field field--inline">
        <span>Время события</span>
        <input type="time" value={eventTime} min={state.now} onChange={(event) => setEventTime(event.target.value)} aria-invalid={error !== null} />
      </label>
      {error && <span className="error-text">{error}</span>}
      <button type="button" className="btn btn-danger" disabled={busy || showPrevious} onClick={() => setDialog('urgent')}>
        Срочная заявка
      </button>
      <button type="button" className="btn" disabled={busy || showPrevious} onClick={() => setDialog('unavailable')}>
        Инженер недоступен
      </button>
      {dialog === 'urgent' && <UrgentRequestDialog onClose={() => setDialog(null)} />}
      {dialog === 'unavailable' && <EngineerUnavailableDialog onClose={() => setDialog(null)} />}
    </div>
  );
}
```

- [ ] **Step 6: Реализовать `frontend/src/components/DiffBanner.tsx`**

```tsx
import { useState } from 'react';
import { describeEvent } from '../lib/events';
import { formatKm } from '../lib/format';
import { byId } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';

export function DiffBanner() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const setShowPrevious = useAppStore((s) => s.setShowPrevious);
  const [dismissedVersion, setDismissedVersion] = useState<number | null>(null);
  if (!state || !state.last_diff || state.version === dismissedVersion) return null;

  const diff = state.last_diff;
  const last = state.events[state.events.length - 1];
  const before = diff.metrics_before;
  const after = diff.metrics_after;
  const shifts = diff.time_shifts.filter((item) => item.delta_min !== 0).length;

  return (
    <div className="diff-banner" role="status">
      <div className="diff-banner__text">
        <strong>{last ? describeEvent(last.event, byId(state.engineers)) : 'План перестроен'}</strong>
        <span>
          Новых назначений: {diff.added.length} · перенесено: {diff.moved.length} · снято: {diff.removed.length} · сдвиг
          времени: {shifts}
        </span>
        <span>
          Инженеров {before.engineers_used} → {after.engineers_used} · пробег {formatKm(before.total_km)} →{' '}
          {formatKm(after.total_km)} · не назначено {before.unassigned} → {after.unassigned}
        </span>
      </div>
      <div className="segmented" role="group" aria-label="Какой план показать">
        <button type="button" aria-pressed={showPrevious} onClick={() => setShowPrevious(true)} disabled={!state.previous_plan}>
          До события
        </button>
        <button type="button" aria-pressed={!showPrevious} onClick={() => setShowPrevious(false)}>
          После события
        </button>
      </div>
      <button
        type="button"
        className="btn btn-ghost btn-small"
        onClick={() => {
          setShowPrevious(false);
          setDismissedVersion(state.version);
        }}
      >
        Скрыть
      </button>
    </div>
  );
}
```

- [ ] **Step 7: Прогнать тесты**

Run: `npx vitest run src/components/events src/components/DiffBanner.test.tsx`
Expected: `Test Files  2 passed (2)`, `Tests  7 passed (7)`.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/events frontend/src/components/DiffBanner.tsx frontend/src/components/DiffBanner.test.tsx
git commit -m "feat(frontend): срочная заявка, недоступность инженера и баннер «до / после»" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```

---

## Task 10: Карта

**Files:**
- Create: `frontend/src/components/map/yandexLoader.ts`, `frontend/src/components/map/useRouteGeometries.ts`, `frontend/src/components/map/MapContent.tsx`, `frontend/src/components/map/MapView.tsx`
- Test: `frontend/src/components/map/MapContent.test.tsx`, `frontend/src/components/map/MapView.test.tsx`

**Interfaces:**
- Consumes: `getRouteGeometry`, `straightLegs`, `assignmentIndex`, `diffMarks`, `displayedPlan`, `engineerColor`, стор (`config`, `state`, `datasetId`, `showPrevious`, `selectedRequestId`, `selectedEngineerId`, `selectRequest`, `selectEngineer`, `pickMode`, `finishPick`).
- Produces: `type LngLat = [number, number]` (порядок lon, lat), `MapLocation {center; zoom; duration?}`, `LineStyle`, `interface YMapsComponents {YMap; YMapDefaultSchemeLayer; YMapDefaultFeaturesLayer; YMapMarker; YMapFeature; YMapListener}`, `loadYandexMaps(apiKey: string): Promise<YMapsComponents>`, `useRouteGeometries(datasetId, state, plan, planKind): Map<string, RouteLeg[]>`, `clearRouteGeometryCache(): void`, `MapContent({components})`, `MapView()`, `MapPlaceholder({title, text?})`.

Что проверено, а что нет: загрузка скрипта и сниппет reactify взяты из документации Яндекса (`ymaps3.import('@yandex/ymaps3-reactify')`, `reactify.bindTo(React, ReactDOM).module(ymaps3)`, координаты `[lon, lat]`, `YMapFeature` c `style.stroke`). Отрисовку `MapContent` проверяют тесты на фейковых компонентах. Реальный рендер карты с ключом в этой проверке не запускался: его закрывает ручной шаг Задачи 12.

- [ ] **Step 1: Написать падающие тесты**

`frontend/src/components/map/MapContent.test.tsx`:

```tsx
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, getRouteGeometry: vi.fn() };
});

import * as api from '../../api/client';
import { useAppStore } from '../../store/useAppStore';
import { makePlanningState, makeRouteGeometry } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { MapContent } from './MapContent';
import { clearRouteGeometryCache } from './useRouteGeometries';
import type { LngLat, LineStyle, MapLocation, YMapsComponents } from './yandexLoader';

// Фейковые компоненты вместо Яндекс Карт: те же пропсы, обычный DOM.
const fake: YMapsComponents = {
  YMap: ({ location, children }: { location: MapLocation; children?: ReactNode }) => (
    <div data-testid="map" data-center={location.center.join(',')}>
      {children}
    </div>
  ),
  YMapDefaultSchemeLayer: () => null,
  YMapDefaultFeaturesLayer: () => null,
  YMapMarker: ({ coordinates, children }: { coordinates: LngLat; children?: ReactNode }) => (
    <div data-testid="marker" data-coordinates={coordinates.join(',')}>
      {children}
    </div>
  ),
  YMapFeature: ({ geometry, style }: { geometry: { coordinates: LngLat[] }; style?: LineStyle }) => (
    <div data-testid="feature" data-coordinates={JSON.stringify(geometry.coordinates)} data-color={style?.stroke[0].color} />
  ),
  YMapListener: ({ onClick }: { onClick?: (object: unknown, event: { coordinates: LngLat }) => void }) => (
    <button type="button" onClick={() => onClick?.(undefined, { coordinates: [37.8, 55.71] })}>
      map-click
    </button>
  ),
};

const firstPoints = () =>
  screen.getAllByTestId('feature').map((feature) => (JSON.parse(feature.getAttribute('data-coordinates') as string) as LngLat[])[0]);

beforeEach(() => {
  vi.resetAllMocks();
  clearRouteGeometryCache();
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('MapContent', () => {
  it('renders engineer start markers, a neutral office marker and request markers', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    render(<MapContent components={fake} />);
    expect(screen.getByTitle('Старт: Бригада Арташкин').parentElement).toHaveAttribute('data-coordinates', '37.781,55.7005');
    expect(screen.getByTitle('Старт: Бригада Комарь')).toHaveClass('marker--dimmed');
    expect(screen.getByTitle('г. Москва, ул Юных Ленинцев, д 83с 4')).toHaveClass('marker--office');
    expect(screen.getByTitle(/^URG-001:/)).toHaveClass('marker--urgent', 'marker--changed');
    expect(screen.getByTitle(/^10135:/)).toHaveTextContent('×');
    expect(screen.getByTitle(/^18754:/)).toHaveClass('marker--unassigned');
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalledTimes(2));
  });

  it('draws legs from each engineer start and replaces them with backend geometry', async () => {
    vi.mocked(api.getRouteGeometry).mockImplementation(async (_datasetId, engineerId) => {
      if (engineerId === 'E02') return makeRouteGeometry();
      throw new Error('offline');
    });
    render(<MapContent components={fake} />);
    expect(firstPoints()).toContainEqual([37.781, 55.7005]);
    expect(firstPoints()).not.toContainEqual([37.7862, 55.7075]);
    const road = JSON.stringify(makeRouteGeometry().legs[0].coordinates);
    await waitFor(() =>
      expect(screen.getAllByTestId('feature').some((feature) => feature.getAttribute('data-coordinates') === road)).toBe(true),
    );
  });

  it('selects requests and engineers from markers and picks a point in pick mode', async () => {
    vi.mocked(api.getRouteGeometry).mockRejectedValue(new Error('offline'));
    render(<MapContent components={fake} />);
    fireEvent.click(screen.getByTitle(/^50104:/));
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
    expect(screen.getByTestId('map')).toHaveAttribute('data-center', '37.7336,55.7212');

    fireEvent.click(screen.getByTitle('Старт: Бригада Белузин'));
    expect(useAppStore.getState().selectedEngineerId).toBe('E02');

    act(() => useAppStore.getState().startPick());
    fireEvent.click(screen.getByRole('button', { name: 'map-click' }));
    expect(useAppStore.getState()).toMatchObject({ pickMode: false, pickedPoint: { lat: 55.71, lon: 37.8 } });
    await waitFor(() => expect(api.getRouteGeometry).toHaveBeenCalled());
  });
});
```

`frontend/src/components/map/MapView.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./yandexLoader', () => ({ loadYandexMaps: vi.fn() }));

import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { MapView } from './MapView';
import { loadYandexMaps } from './yandexLoader';

beforeEach(() => {
  vi.resetAllMocks();
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('MapView', () => {
  it('waits for the client config', () => {
    render(<MapView />);
    expect(screen.getByText('Загружаем настройки карты…')).toBeInTheDocument();
  });

  it('explains how to enable the map when there is no API key', () => {
    resetStore({ state: makePlanningState(), config: { yandex_maps_api_key: null, llm_enabled: false, osrm_available: true } });
    render(<MapView />);
    expect(screen.getByText('Карта отключена')).toBeInTheDocument();
    expect(loadYandexMaps).not.toHaveBeenCalled();
  });

  it('shows the loader error when Yandex Maps fail to load', async () => {
    vi.mocked(loadYandexMaps).mockRejectedValue(new Error('Не удалось загрузить Яндекс Карты. Проверьте интернет и ключ API.'));
    resetStore({ state: makePlanningState(), config: { yandex_maps_api_key: 'test-key', llm_enabled: false, osrm_available: true } });
    render(<MapView />);
    expect(await screen.findByText('Карта не загрузилась')).toBeInTheDocument();
    expect(loadYandexMaps).toHaveBeenCalledWith('test-key');
  });
});
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `npx vitest run src/components/map`
Expected: FAIL с `Failed to resolve import "./MapContent"` и `Failed to resolve import "./yandexLoader"`.

- [ ] **Step 3: Реализовать загрузчик `frontend/src/components/map/yandexLoader.ts`**

Глобальный `ymaps3` типизируется пакетом `@yandex/ymaps3-types`, подключённым в `tsconfig.json` через `types`.

```ts
import * as React from 'react';
import * as ReactDOM from 'react-dom';
import type { ComponentType, ReactNode } from 'react';

/** [lon, lat] */
export type LngLat = [number, number];

export interface MapLocation {
  center: LngLat;
  zoom: number;
  duration?: number;
}

export interface LineStyle {
  stroke: { color: string; width: number }[];
}

/** Узкий интерфейс компонентов Яндекс Карт, которыми пользуется приложение. */
export interface YMapsComponents {
  YMap: ComponentType<{ location: MapLocation; mode?: 'vector' | 'raster'; children?: ReactNode }>;
  YMapDefaultSchemeLayer: ComponentType;
  YMapDefaultFeaturesLayer: ComponentType;
  YMapMarker: ComponentType<{ coordinates: LngLat; zIndex?: number; children?: ReactNode }>;
  YMapFeature: ComponentType<{ geometry: { type: 'LineString'; coordinates: LngLat[] }; style?: LineStyle }>;
  YMapListener: ComponentType<{ layer?: string; onClick?: (object: unknown, event: { coordinates: LngLat }) => void }>;
}

let pending: Promise<YMapsComponents> | null = null;

function injectScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if ('ymaps3' in window) {
      resolve();
      return;
    }
    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Не удалось загрузить Яндекс Карты. Проверьте интернет и ключ API.'));
    document.head.appendChild(script);
  });
}

export function loadYandexMaps(apiKey: string): Promise<YMapsComponents> {
  if (!pending) {
    pending = (async () => {
      await injectScript(`https://api-maps.yandex.ru/v3/?apikey=${encodeURIComponent(apiKey)}&lang=ru_RU`);
      const [ymaps3React] = await Promise.all([ymaps3.import('@yandex/ymaps3-reactify'), ymaps3.ready]);
      const reactify = ymaps3React.reactify.bindTo(React, ReactDOM);
      const module = reactify.module(ymaps3);
      return {
        YMap: module.YMap,
        YMapDefaultSchemeLayer: module.YMapDefaultSchemeLayer,
        YMapDefaultFeaturesLayer: module.YMapDefaultFeaturesLayer,
        YMapMarker: module.YMapMarker,
        YMapFeature: module.YMapFeature,
        YMapListener: module.YMapListener,
      } as unknown as YMapsComponents;
    })();
    pending.catch(() => {
      pending = null;
    });
  }
  return pending;
}
```

- [ ] **Step 4: Реализовать `frontend/src/components/map/useRouteGeometries.ts`**

Сначала рисуются прямые отрезки от старта инженера, затем они заменяются линиями дорог из `GET /api/datasets/{id}/routes/{engineer_id}/geometry`. Ошибка запроса оставляет прямые отрезки.

```ts
import { useEffect, useState } from 'react';
import { getRouteGeometry } from '../../api/client';
import type { Plan, PlanningState, RouteLeg } from '../../api/types';
import { byId, straightLegs } from '../../lib/planView';

const cache = new Map<string, RouteLeg[]>();

/** Для тестов: кэш живёт на уровне модуля. */
export function clearRouteGeometryCache(): void {
  cache.clear();
}

/** Геометрия маршрутов по инженерам: сразу прямые отрезки, затем линии дорог с backend. */
export function useRouteGeometries(
  datasetId: string | null,
  state: PlanningState | null,
  plan: Plan | null,
  planKind: 'current' | 'previous',
): Map<string, RouteLeg[]> {
  const [legs, setLegs] = useState<Map<string, RouteLeg[]>>(() => new Map());

  useEffect(() => {
    if (!datasetId || !state || !plan) {
      setLegs(new Map());
      return;
    }
    let alive = true;
    const requests = byId(state.requests);
    const engineers = byId(state.engineers);
    const routes = plan.routes.filter((route) => route.visits.length > 0);
    const keyOf = (engineerId: string) => `${datasetId}:${state.version}:${planKind}:${engineerId}`;

    const initial = new Map<string, RouteLeg[]>();
    for (const route of routes) {
      const engineer = engineers.get(route.engineer_id);
      if (engineer) initial.set(route.engineer_id, cache.get(keyOf(route.engineer_id)) ?? straightLegs(route, engineer, requests));
    }
    setLegs(initial);

    void Promise.all(
      routes.map(async (route) => {
        const key = keyOf(route.engineer_id);
        if (cache.has(key)) return;
        try {
          const geometry = await getRouteGeometry(datasetId, route.engineer_id, planKind);
          cache.set(key, geometry.legs);
        } catch {
          // остаются прямые отрезки
        }
      }),
    ).then(() => {
      if (!alive) return;
      setLegs((previous) => {
        const next = new Map(previous);
        for (const route of routes) {
          const cached = cache.get(keyOf(route.engineer_id));
          if (cached) next.set(route.engineer_id, cached);
        }
        return next;
      });
    });

    return () => {
      alive = false;
    };
  }, [datasetId, state, plan, planKind]);

  return legs;
}
```

- [ ] **Step 5: Реализовать `frontend/src/components/map/MapContent.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { engineerColor } from '../../lib/colors';
import { shortAddress } from '../../lib/format';
import { assignmentIndex, diffMarks, displayedPlan, engineerIdsOf, type DiffMark } from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';
import { useRouteGeometries } from './useRouteGeometries';
import type { LngLat, MapLocation, YMapsComponents } from './yandexLoader';

const MOSCOW_CENTER: LngLat = [37.6176, 55.7558];

export function MapContent({ components }: { components: YMapsComponents }) {
  const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapMarker, YMapFeature, YMapListener } = components;
  const state = useAppStore((s) => s.state);
  const datasetId = useAppStore((s) => s.datasetId);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const selectEngineer = useAppStore((s) => s.selectEngineer);
  const pickMode = useAppStore((s) => s.pickMode);
  const finishPick = useAppStore((s) => s.finishPick);
  const plan = state ? displayedPlan(state, showPrevious) : null;
  const legs = useRouteGeometries(datasetId, state, plan, showPrevious ? 'previous' : 'current');
  const [location, setLocation] = useState<MapLocation>(() => ({
    center: state ? [state.office.lon, state.office.lat] : MOSCOW_CENTER,
    zoom: 10,
  }));

  useEffect(() => {
    if (!selectedRequestId) return;
    const request = useAppStore.getState().state?.requests.find((item) => item.id === selectedRequestId);
    if (request && request.lat !== null && request.lon !== null) {
      setLocation({ center: [request.lon, request.lat], zoom: 14, duration: 400 });
    }
  }, [selectedRequestId]);

  if (!state || !plan) return null;
  const ids = engineerIdsOf(state);
  const assignments = assignmentIndex(plan);
  const marks: Map<string, DiffMark> = showPrevious ? new Map() : diffMarks(state.last_diff);

  const onMapClick = (_object: unknown, event: { coordinates: LngLat }) => {
    if (useAppStore.getState().pickMode) finishPick({ lon: event.coordinates[0], lat: event.coordinates[1] });
  };

  return (
    <YMap location={location} mode="vector">
      <YMapDefaultSchemeLayer />
      <YMapDefaultFeaturesLayer />
      {plan.routes.flatMap((route) => {
        const color = engineerColor(route.engineer_id, ids);
        const dimmed = selectedEngineerId !== null && selectedEngineerId !== route.engineer_id;
        const width = selectedEngineerId === route.engineer_id ? 6 : 3;
        return (legs.get(route.engineer_id) ?? []).map((leg, index) => (
          <YMapFeature
            key={`${route.engineer_id}-${index}-${leg.to_request_id}`}
            geometry={{ type: 'LineString', coordinates: leg.coordinates }}
            style={{ stroke: [{ color: dimmed ? `${color}40` : color, width }] }}
          />
        ));
      })}
      <YMapMarker coordinates={[state.office.lon, state.office.lat]} zIndex={20}>
        <div className="marker marker--office" title={state.office.address}>
          Офис
        </div>
      </YMapMarker>
      {state.engineers.map((engineer) => {
        const color = engineerColor(engineer.id, ids);
        const dimmed = !engineer.available || (selectedEngineerId !== null && selectedEngineerId !== engineer.id);
        return (
          <YMapMarker key={`start-${engineer.id}`} coordinates={[engineer.start_lon, engineer.start_lat]} zIndex={30}>
            <div
              className={`marker marker--start${dimmed ? ' marker--dimmed' : ''}`}
              style={{ borderColor: color, color }}
              title={`Старт: ${engineer.name}`}
              onClick={(event) => {
                event.stopPropagation();
                if (!pickMode) selectEngineer(selectedEngineerId === engineer.id ? null : engineer.id);
              }}
            >
              ⌂
            </div>
          </YMapMarker>
        );
      })}
      {state.requests.map((request) => {
        if (request.lat === null || request.lon === null) return null;
        const info = assignments.get(request.id);
        const cancelled = request.status === 'cancelled';
        const classes = [
          'marker',
          info ? '' : 'marker--unassigned',
          request.priority === 'urgent' ? 'marker--urgent' : '',
          cancelled ? 'marker--cancelled' : '',
          marks.has(request.id) ? 'marker--changed' : '',
          request.id === selectedRequestId ? 'marker--selected' : '',
          selectedEngineerId && info?.engineerId !== selectedEngineerId ? 'marker--dimmed' : '',
        ]
          .filter(Boolean)
          .join(' ');
        return (
          <YMapMarker key={request.id} coordinates={[request.lon, request.lat]} zIndex={request.id === selectedRequestId ? 100 : 10}>
            <div
              className={classes}
              style={{ background: cancelled ? '#ffffff' : engineerColor(info?.engineerId, ids) }}
              title={`${request.id}: ${shortAddress(request.address)}`}
              onClick={(event) => {
                event.stopPropagation();
                if (!pickMode) selectRequest(request.id);
              }}
            >
              {cancelled ? '×' : info ? info.order + 1 : '!'}
            </div>
          </YMapMarker>
        );
      })}
      <YMapListener layer="any" onClick={onMapClick} />
    </YMap>
  );
}
```

- [ ] **Step 6: Реализовать `frontend/src/components/map/MapView.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { useAppStore } from '../../store/useAppStore';
import { MapContent } from './MapContent';
import { loadYandexMaps, type YMapsComponents } from './yandexLoader';

export function MapPlaceholder({ title, text }: { title: string; text?: string }) {
  return (
    <div className="map-placeholder">
      <h3>{title}</h3>
      {text && <p>{text}</p>}
    </div>
  );
}

function YandexMap({ apiKey }: { apiKey: string }) {
  const [components, setComponents] = useState<YMapsComponents | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    loadYandexMaps(apiKey)
      .then((loaded) => {
        if (alive) setComponents(loaded);
      })
      .catch((error: unknown) => {
        if (alive) setLoadError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      alive = false;
    };
  }, [apiKey]);

  if (loadError) return <MapPlaceholder title="Карта не загрузилась" text={loadError} />;
  if (!components) return <MapPlaceholder title="Загружаем Яндекс Карты…" />;
  return <MapContent components={components} />;
}

export function MapView() {
  const config = useAppStore((s) => s.config);
  if (!config) return <MapPlaceholder title="Загружаем настройки карты…" />;
  if (!config.yandex_maps_api_key) {
    return (
      <MapPlaceholder
        title="Карта отключена"
        text="Ключ Яндекс Карт не задан. Добавьте YANDEX_MAPS_API_KEY в .env и перезапустите backend. Список заявок, таймлайн и перепланирование работают без карты."
      />
    );
  }
  return <YandexMap apiKey={config.yandex_maps_api_key} />;
}
```

- [ ] **Step 7: Прогнать тесты и проверку типов**

Run: `npx vitest run src/components/map && npx tsc --noEmit`
Expected: `Test Files  2 passed (2)`, `Tests  6 passed (6)`, `tsc` без вывода. Предупреждения React про `act(...)` из асинхронной подгрузки геометрии допустимы и тест не роняют.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/map
git commit -m "feat(frontend): карта Яндекса со стартами инженеров, маршрутами и выбором точки" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```

---

## Task 11: Каркас основного экрана, стили и сборка

**Files:**
- Create: `frontend/src/components/MetricsStrip.tsx`, `frontend/src/components/ErrorToast.tsx`, `frontend/src/components/MainScreen.tsx`, `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: все компоненты Задач 6–10, стор.
- Produces: `App()` (экран загрузки, пока нет `state`, иначе `MainScreen`), `MainScreen()`, `MetricsStrip()`, `ErrorToast()`, точка входа `main.tsx`.

- [ ] **Step 1: Написать падающий тест `frontend/src/App.test.tsx`**

```tsx
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api/client')>();
  return { ...actual, getConfig: vi.fn(), getExplanation: vi.fn(), getRouteGeometry: vi.fn() };
});

import * as api from './api/client';
import { App } from './App';
import { makePlanningState } from './test/fixtures';
import { resetStore } from './test/store';

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getConfig).mockResolvedValue({ yandex_maps_api_key: null, llm_enabled: false, osrm_available: true });
  resetStore();
});

describe('App', () => {
  it('starts on the upload screen', async () => {
    render(<App />);
    expect(screen.getByRole('heading', { name: 'Планирование маршрутов выездных инженеров' })).toBeInTheDocument();
    await vi.waitFor(() => expect(api.getConfig).toHaveBeenCalled());
  });

  it('shows the main screen with metrics, event actions, tabs and the map area once a plan exists', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState() });
    render(<App />);
    expect(screen.getByText('Восток')).toBeInTheDocument();
    expect(screen.getByText('Сейчас 13:00')).toBeInTheDocument();
    expect(screen.getByText('2 из 3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Срочная заявка' })).toBeInTheDocument();
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Заявки', 'Таймлайн', 'Неназначенные1', 'Сравнение']);
    expect(await screen.findByText('Карта отключена')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `npx vitest run src/App.test.tsx`
Expected: FAIL с `Failed to resolve import "./App"`.

- [ ] **Step 3: Реализовать `frontend/src/components/MetricsStrip.tsx`**

```tsx
import { formatKm, formatSigned } from '../lib/format';
import { displayedPlan } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';

interface MetricProps {
  label: string;
  value: string;
  delta?: string;
  warn?: boolean;
}

function Metric({ label, value, delta, warn }: MetricProps) {
  return (
    <div className={`metric${warn ? ' metric--warn' : ''}`}>
      <span className="metric__label">{label}</span>
      <span className="metric__value">{value}</span>
      {delta !== undefined && (
        <span className="metric__delta" title="Разница с базовым вариантом (FCFS)">
          {delta} к базовому
        </span>
      )}
    </div>
  );
}

export function MetricsStrip() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const busy = useAppStore((s) => s.busy);
  const plan = useAppStore((s) => s.plan);
  const reset = useAppStore((s) => s.reset);
  if (!state) return null;

  const current = displayedPlan(state, showPrevious).metrics;
  const base = state.baseline.metrics;
  return (
    <div className="metrics-strip">
      <div className="metrics-strip__title">
        <strong>{state.office.title}</strong>
        <span className="muted">
          Сейчас {state.now}
          {showPrevious ? ' · показан план до события' : ''}
        </span>
      </div>
      <Metric
        label="Инженеров"
        value={`${current.engineers_used} из ${state.engineers.length}`}
        delta={formatSigned(current.engineers_used - base.engineers_used)}
      />
      <Metric label="Пробег" value={formatKm(current.total_km)} delta={`${formatSigned(current.total_km - base.total_km, 1)} км`} />
      <Metric label="Назначено" value={String(current.assigned)} />
      <Metric label="Не назначено" value={String(current.unassigned)} warn={current.unassigned > 0} />
      <div className="metrics-strip__actions">
        <button type="button" className="btn" onClick={() => void plan()} disabled={busy}>
          Пересчитать с нуля
        </button>
        <button type="button" className="btn btn-ghost" onClick={reset}>
          Другой файл
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Реализовать `frontend/src/components/ErrorToast.tsx`**

```tsx
import { useAppStore } from '../store/useAppStore';

export function ErrorToast() {
  const error = useAppStore((s) => s.error);
  const clearError = useAppStore((s) => s.clearError);
  if (!error) return null;
  return (
    <div className="toast" role="alert">
      <span>{error}</span>
      <button type="button" className="btn btn-ghost btn-small" onClick={clearError} aria-label="Закрыть сообщение">
        ✕
      </button>
    </div>
  );
}
```

- [ ] **Step 5: Реализовать `frontend/src/components/MainScreen.tsx`**

```tsx
import { useAppStore } from '../store/useAppStore';
import { DiffBanner } from './DiffBanner';
import { ErrorToast } from './ErrorToast';
import { EventToolbar } from './events/EventToolbar';
import { MapView } from './map/MapView';
import { MetricsStrip } from './MetricsStrip';
import { RightPanel } from './panel/RightPanel';

export function MainScreen() {
  const pickMode = useAppStore((s) => s.pickMode);
  return (
    <div className="app-shell">
      <header className="topbar">
        <MetricsStrip />
        <EventToolbar />
      </header>
      <DiffBanner />
      <div className="workspace">
        <section className={`map-area${pickMode ? ' map-area--picking' : ''}`} aria-label="Карта">
          {pickMode && <div className="map-hint">Кликните по карте, чтобы указать место срочной заявки</div>}
          <MapView />
        </section>
        <RightPanel />
      </div>
      <ErrorToast />
    </div>
  );
}
```

- [ ] **Step 6: Реализовать `frontend/src/App.tsx` и `frontend/src/main.tsx`**

`frontend/src/App.tsx`:

```tsx
import { useEffect } from 'react';
import { MainScreen } from './components/MainScreen';
import { UploadScreen } from './components/UploadScreen';
import { useAppStore } from './store/useAppStore';

export function App() {
  const loadConfig = useAppStore((s) => s.loadConfig);
  const hasPlan = useAppStore((s) => s.state !== null);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  return hasPlan ? <MainScreen /> : <UploadScreen />;
}
```

`frontend/src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './styles.css';

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 7: Создать стили `frontend/src/styles.css`**

```css
:root {
  --bg: #f4f5f7;
  --surface: #ffffff;
  --border: #e3e6eb;
  --text: #16191f;
  --muted: #6b7280;
  --primary: #1f5eff;
  --primary-soft: #e8efff;
  --danger: #d92d20;
  --danger-soft: #fdecea;
  --warn: #b54708;
  --warn-soft: #fef4e6;
  --ok: #067647;
  --radius: 8px;
  --shadow: 0 1px 2px rgba(16, 24, 40, 0.06), 0 4px 12px rgba(16, 24, 40, 0.06);
  font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  font-size: 14px;
  color: var(--text);
  background: var(--bg);
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
}

h1,
h2,
h3,
h4 {
  margin: 0 0 8px;
  line-height: 1.25;
}

h4 {
  margin-top: 16px;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
}

.muted {
  color: var(--muted);
}

.error-text {
  color: var(--danger);
}

.warn-text {
  color: var(--warn);
}

.note {
  background: var(--warn-soft);
  color: var(--warn);
  padding: 6px 10px;
  border-radius: var(--radius);
}

.empty {
  padding: 24px;
  text-align: center;
  color: var(--muted);
}

/* Кнопки и поля */
.btn {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  border-radius: var(--radius);
  padding: 7px 12px;
  font: inherit;
  cursor: pointer;
  white-space: nowrap;
}

.btn:hover:not(:disabled) {
  border-color: #c5cad3;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-primary {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
}

.btn-danger {
  background: var(--danger);
  border-color: var(--danger);
  color: #fff;
}

.btn-ghost {
  background: transparent;
  border-color: transparent;
}

.btn-small {
  padding: 4px 8px;
  font-size: 12px;
}

.btn-large {
  padding: 12px 24px;
  font-size: 16px;
  margin-top: 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
  flex: 1;
}

.field input,
.field select {
  font: inherit;
  font-size: 14px;
  color: var(--text);
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
}

.field--inline {
  flex-direction: row;
  align-items: center;
  gap: 8px;
  flex: none;
}

.field-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  margin: 8px 0;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: none;
  display: inline-block;
}

.badges {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  background: #eef0f3;
  color: #394150;
}

.badge--urgent {
  background: var(--danger-soft);
  color: var(--danger);
}

.badge--cancelled {
  background: #eef0f3;
  color: var(--muted);
  text-decoration: line-through;
}

.badge--diff {
  background: var(--primary-soft);
  color: var(--primary);
}

.badge--warn {
  background: var(--warn-soft);
  color: var(--warn);
}

/* Экран загрузки */
.upload-screen {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.upload-card {
  width: min(720px, 100%);
  background: var(--surface);
  border-radius: 12px;
  box-shadow: var(--shadow);
  padding: 32px;
}

.dropzone {
  margin: 20px 0;
  border: 2px dashed var(--border);
  border-radius: 12px;
  padding: 28px;
  text-align: center;
}

.dropzone--active {
  border-color: var(--primary);
  background: var(--primary-soft);
}

.upload-status__row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
}

.progress {
  height: 6px;
  border-radius: 3px;
  background: #eef0f3;
  overflow: hidden;
}

.progress__bar {
  height: 100%;
  background: var(--primary);
  transition: width 0.3s;
}

.report {
  margin-top: 20px;
  border-top: 1px solid var(--border);
  padding-top: 16px;
}

.report__grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin: 0 0 12px;
}

.report__grid dt {
  font-size: 12px;
  color: var(--muted);
}

.report__grid dd {
  margin: 2px 0 0;
  font-weight: 600;
}

/* Основной экран */
.app-shell {
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}

.metrics-strip {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}

.metrics-strip__title {
  display: flex;
  flex-direction: column;
}

.metrics-strip__actions {
  display: flex;
  gap: 6px;
}

.metric {
  display: flex;
  flex-direction: column;
  min-width: 90px;
}

.metric__label {
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.metric__value {
  font-size: 18px;
  font-weight: 650;
}

.metric__delta {
  font-size: 11px;
  color: var(--muted);
}

.metric--warn .metric__value {
  color: var(--warn);
}

.event-toolbar {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
}

.diff-banner {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 16px;
  background: var(--primary-soft);
  border-bottom: 1px solid #cfdcff;
}

.diff-banner__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}

.segmented {
  display: inline-flex;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  background: var(--surface);
}

.segmented button {
  border: 0;
  background: transparent;
  padding: 6px 12px;
  font: inherit;
  cursor: pointer;
}

.segmented button[aria-pressed='true'] {
  background: var(--primary);
  color: #fff;
}

.workspace {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(440px, 42%);
}

.map-area {
  position: relative;
  min-height: 0;
}

.map-area--picking {
  cursor: crosshair;
}

.map-hint {
  position: absolute;
  z-index: 20;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--text);
  color: #fff;
  padding: 6px 12px;
  border-radius: var(--radius);
}

.map-placeholder {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 32px;
  color: var(--muted);
  background: repeating-linear-gradient(45deg, #eceef2, #eceef2 12px, #f2f3f6 12px, #f2f3f6 24px);
}

.map-placeholder p {
  max-width: 420px;
}

.marker {
  transform: translate(-50%, -50%);
  min-width: 24px;
  height: 24px;
  padding: 0 4px;
  border-radius: 12px;
  border: 2px solid #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.35);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.marker--office {
  background: #f3f4f6;
  color: var(--muted);
  border: 1px solid #c5cad3;
  border-radius: 4px;
  padding: 0 6px;
  font-weight: 500;
  font-size: 11px;
  box-shadow: none;
}

.marker--start {
  background: #ffffff;
  border-width: 3px;
  border-radius: 6px;
  min-width: 24px;
  font-size: 14px;
  line-height: 1;
}

.marker--urgent {
  border-color: var(--danger);
  box-shadow: 0 0 0 3px rgba(217, 45, 32, 0.35);
}

.marker--cancelled {
  color: var(--muted);
  border-color: var(--muted);
  opacity: 0.7;
}

.marker--changed {
  outline: 3px solid var(--primary);
  outline-offset: 2px;
}

.marker--selected {
  transform: translate(-50%, -50%) scale(1.35);
}

.marker--dimmed {
  opacity: 0.35;
}

/* Правая панель */
.panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--surface);
  border-left: 1px solid var(--border);
}

.tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  padding: 0 8px;
}

.tabs__tab {
  border: 0;
  background: transparent;
  padding: 10px 12px;
  font: inherit;
  color: var(--muted);
  cursor: pointer;
  border-bottom: 2px solid transparent;
}

.tabs__tab--active {
  color: var(--text);
  border-bottom-color: var(--primary);
  font-weight: 600;
}

.tabs__badge {
  margin-left: 6px;
  background: var(--warn-soft);
  color: var(--warn);
  border-radius: 999px;
  padding: 0 6px;
  font-size: 11px;
}

.panel__content {
  flex: 1;
  overflow: auto;
  padding: 12px;
}

.tab-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.request-list,
.unassigned-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.request-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 8px;
  border-radius: var(--radius);
  cursor: pointer;
  border: 1px solid transparent;
}

.request-row:hover {
  background: #f7f8fa;
}

.request-row--selected {
  border-color: var(--primary);
  background: var(--primary-soft);
}

.request-row--cancelled {
  opacity: 0.6;
}

.request-row--changed {
  box-shadow: inset 3px 0 0 var(--primary);
}

.request-row .dot {
  margin-top: 5px;
}

.request-row__main {
  flex: 1;
  min-width: 0;
}

.request-row__title {
  display: flex;
  gap: 8px;
}

.request-row__title span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.request-row__meta {
  display: flex;
  gap: 12px;
  color: var(--muted);
  font-size: 12px;
  margin: 2px 0 4px;
}

.unassigned-item {
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 8px;
  cursor: pointer;
}

.unassigned-item__head {
  display: flex;
  justify-content: space-between;
}

.unassigned-item p {
  margin: 6px 0 0;
}

.explanation {
  border-bottom: 1px solid var(--border);
  padding: 12px;
  max-height: 55%;
  overflow: auto;
  background: #fbfcfd;
}

.explanation__head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.explanation__summary {
  font-size: 15px;
  font-weight: 500;
}

.checks,
.factors {
  margin: 8px 0;
  padding: 0;
  list-style: none;
}

.factors li::before {
  content: '• ';
  color: var(--primary);
}

.table td {
  font-variant-numeric: tabular-nums;
}

.check {
  display: grid;
  grid-template-columns: 18px 120px 1fr;
  gap: 6px;
  padding: 2px 0;
}

.check--ok span:first-child {
  color: var(--ok);
}

.check--fail span:first-child {
  color: var(--danger);
}

.table {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
}

.table th,
.table td {
  text-align: left;
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
}

.table th {
  font-size: 12px;
  color: var(--muted);
  font-weight: 500;
}

.table--compact th,
.table--compact td {
  padding: 4px 6px;
  font-size: 12px;
}

.delta--better {
  color: var(--ok);
  font-weight: 600;
}

.delta--worse {
  color: var(--danger);
}

.delta--same {
  color: var(--muted);
}

/* Таймлайн */
.timeline__row {
  display: grid;
  grid-template-columns: 150px 1fr;
  align-items: center;
  height: 34px;
}

.timeline__row--header {
  height: 22px;
}

.timeline__label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  overflow: hidden;
  white-space: nowrap;
}

.timeline__track {
  position: relative;
  height: 100%;
  border-left: 1px solid var(--border);
}

.timeline__tick {
  position: absolute;
  transform: translateX(-50%);
  font-size: 10px;
  color: var(--muted);
}

.timeline__shift {
  position: absolute;
  top: 6px;
  bottom: 6px;
  background: #f1f3f6;
  border-radius: 4px;
}

.timeline__unavailable {
  position: absolute;
  top: 6px;
  bottom: 6px;
  right: 0;
  background: repeating-linear-gradient(45deg, rgba(107, 114, 128, 0.25), rgba(107, 114, 128, 0.25) 4px, transparent 4px, transparent 8px);
}

.timeline__window {
  position: absolute;
  top: 4px;
  bottom: 4px;
  border: 1px dashed;
  border-radius: 4px;
  opacity: 0.6;
}

.timeline__bar {
  position: absolute;
  top: 9px;
  bottom: 9px;
  border: 0;
  border-radius: 3px;
  padding: 0;
  cursor: pointer;
}

.timeline__bar--pinned {
  background-image: repeating-linear-gradient(45deg, rgba(255, 255, 255, 0.45), rgba(255, 255, 255, 0.45) 3px, transparent 3px, transparent 6px) !important;
}

.timeline__bar--urgent {
  box-shadow: 0 0 0 2px var(--danger);
}

.timeline__bar--clipped {
  border-right: 3px dashed var(--warn);
}

.timeline__bar--late {
  outline: 2px solid var(--warn);
}

.timeline__bar--selected {
  top: 5px;
  bottom: 5px;
  box-shadow: 0 0 0 2px var(--text);
}

.timeline__now {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--danger);
}

.timeline__legend {
  margin-top: 12px;
  font-size: 12px;
}

/* Диалоги и уведомления */
.dialog {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 40;
  width: 460px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: var(--shadow);
  padding: 16px;
}

.dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}

.error-list {
  color: var(--danger);
  margin: 8px 0 0;
  padding-left: 18px;
}

.toast {
  position: fixed;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 60;
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--text);
  color: #fff;
  padding: 10px 14px;
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}

.toast .btn-ghost {
  color: #fff;
}

@media (max-width: 1180px) {
  .workspace {
    grid-template-columns: minmax(0, 1fr) 420px;
  }

  .report__grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
```

- [ ] **Step 8: Прогнать весь набор тестов**

Run: `npm test`
Expected: `Test Files  17 passed (17)`, `Tests  73 passed (73)`.

- [ ] **Step 9: Собрать production-бандл**

Run: `npm run build`
Expected: `tsc --noEmit` без ошибок, затем `vite v7.3.6 building client environment for production...` и `✓ built in ...`; в `dist/` лежат `index.html`, `assets/index-*.css` (около 11 кБ) и `assets/index-*.js` (около 185 кБ, gzip около 60 кБ).

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/MetricsStrip.tsx frontend/src/components/ErrorToast.tsx frontend/src/components/MainScreen.tsx frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/main.tsx frontend/src/styles.css
git commit -m "feat(frontend): основной экран, метрики и стили" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```

---

## Task 12: Docker-образ и проверка демо-сценария

**Files:**
- Create: `frontend/Dockerfile`, `frontend/nginx.conf`, `frontend/.dockerignore`

**Interfaces:**
- Consumes: `npm run build` из Задачи 11.
- Produces: образ, который слушает порт 80, раздаёт SPA с fallback на `index.html` и проксирует `/api/` на `http://backend:8001` через DNS Docker. План 2 подключает его в compose сервисом `frontend` с публикацией `8000:80` и сервисом `backend` на порту 8001 в той же сети.

- [ ] **Step 1: Создать `frontend/nginx.conf`**

`resolver` с переменной позволяет nginx стартовать, даже если backend ещё не поднят, а `resolver_timeout 2s` не даёт запросу висеть 30 секунд, если DNS Docker недоступен.

```nginx
server {
    listen 80;
    server_name _;
    client_max_body_size 20m;

    # Docker DNS: backend резолвится при запросе, nginx стартует даже если backend ещё не поднят
    resolver 127.0.0.11 valid=10s ipv6=off;
    resolver_timeout 2s;
    set $backend_upstream http://backend:8001;

    location /api/ {
        proxy_pass $backend_upstream;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 180s;
    }

    location / {
        root /usr/share/nginx/html;
        try_files $uri /index.html;
    }
}
```

- [ ] **Step 2: Создать `frontend/Dockerfile` и `frontend/.dockerignore`**

`frontend/Dockerfile`:

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

`frontend/.dockerignore`:

```gitignore
node_modules
dist
coverage
```

- [ ] **Step 3: Собрать образ**

Run: `cd frontend && docker build -t routing-frontend .`
Expected: сборка завершается строкой `naming to docker.io/library/routing-frontend:latest done`.

- [ ] **Step 4: Проверить раздачу и прокси в сети, как в compose**

Run:
```bash
docker network create routing-check
docker run -d --rm --name backend --network routing-check python:3.12-slim python -m http.server 8001
docker run -d --rm --name frontend-check --network routing-check -p 18080:80 routing-frontend
sleep 3
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:18080/
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:18080/any/route
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:18080/api/health
docker rm -f frontend-check backend
docker network rm routing-check
```
Expected: `200`, `200`, `404`. Код 404 отдаёт тестовый `http.server` на месте backend: запрос дошёл через nginx, прокси работает. Если порт или имя `backend` заняты запущенным compose Плана 2, сначала остановите его.

- [ ] **Step 5: Ручная проверка демо-сценария ТЗ на живом backend**

Выполняется, когда backend Плана 2 отвечает на `http://localhost:8001/api/health`. Запустите `npm run dev` и откройте `http://localhost:5173` (или `docker compose up` Плана 2 и `http://localhost:8000`). Для карты в `.env` backend должен быть задан `YANDEX_MAPS_API_KEY`.

1. Загрузите `data/raw/east_synthetic.csv`. Ожидание: этапы «Чтение файла», «Поиск адресов на карте», «Расчёт расстояний», затем отчёт «Восток» и кнопка «Спланировать».
2. Нажмите «Спланировать». Ожидание: слева карта Москвы с офисом, стартовыми маркерами инженеров и цветными маршрутами; справа список заявок с инженерами; сверху метрики с разницей к базовому.
3. Выберите инженера в фильтре вкладки «Заявки». Ожидание: его маршрут выделен, остальные приглушены.
4. Кликните по заявке. Ожидание: карта центрируется на ней, открывается карточка с проверками «Навык», «Транспорт», «Временное окно», «Смена» и таблицей других инженеров.
5. Поставьте «Время события» 13:00 и нажмите «Срочная заявка», укажите точку на карте, нажмите «Добавить и перепланировать». Ожидание: баннер с числом переносов и метриками до/после, новая заявка на карте с красной обводкой, перенесённые заявки подсвечены.
6. Переключите «До события» и «После события». Ожидание: карта, список и таймлайн показывают соответствующий план.
7. Откройте «Сравнение». Ожидание: три колонки «Базовый (FCFS)», «Оптимизированный», «Диспетчеры» с дельтой к базовому.

Если backend ещё не готов, отметьте шаг как отложенный и вернитесь к нему после Плана 2.

- [ ] **Step 6: Commit**

```bash
git add frontend/Dockerfile frontend/nginx.conf frontend/.dockerignore
git commit -m "build(frontend): nginx-образ с SPA и прокси /api в backend" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ"
```
