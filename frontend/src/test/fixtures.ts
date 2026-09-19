import type {
  DatasetStatus,
  DelayForecast,
  Engineer,
  EventChoice,
  EventVariant,
  Explanation,
  Metrics,
  PlanEvent,
  PlanningState,
  Route,
  RouteGeometry,
  ServiceRequest,
  Skill,
  TimelineItem,
  VariantOption,
  Visit,
} from '../api/types';

const OFFICE = { region: 'east', title: 'Восток', address: 'г. Москва, ул Юных Ленинцев, д 83с 4', lat: 55.7075, lon: 37.7862 };

function visit(requestId: string, arrival: string, start: string, end: string, legKm: number, legMin: number, pinned = false): Visit {
  return { request_id: requestId, arrival, start, end, leg_km: legKm, leg_min: legMin, late_min: 0, pinned };
}

function route(engineerId: string, visits: Visit[], lunch: Route['lunch'] = null): Route {
  return {
    engineer_id: engineerId,
    visits,
    total_km: Number(visits.reduce((sum, item) => sum + item.leg_km, 0).toFixed(2)),
    total_travel_min: visits.reduce((sum, item) => sum + item.leg_min, 0),
    lunch,
  };
}

const lunch = (start: string, end: string): Route['lunch'] => ({ start, end });

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
    asap: false,
    priority: 'normal',
    // Уровень распределения идёт за типом работ: подключение выше ремонта, авария выше подключения.
    tier: skill === 'connection' ? 'connection' : skill === 'emergency' ? 'emergency' : 'routine',
    skill,
    transport_required: null,
    status: 'active',
    source_type_bk: skill === 'local' ? 'Локальная заявка' : 'Подключение',
    source_type_hd: skill === 'local' ? 'Нет линка' : 'Конвергенция абонента',
    needs_equipment: false,
    ...extra,
  };
}

function engineers(): Engineer[] {
  // Старт каждого инженера: медоид адресов его бригады, а не офис региона.
  return [
    { id: 'E01', name: 'Бригада Арташкин', start_lat: 55.7005, start_lon: 37.781, shift_start: '10:00', shift_end: '22:00', skills: ['local', 'connection'], transport: 'car', available: true, unavailable_from: null, equipment_stock: 6 },
    { id: 'E02', name: 'Бригада Белузин', start_lat: 55.745, start_lon: 37.802, shift_start: '10:00', shift_end: '22:00', skills: ['local', 'connection', 'emergency'], transport: 'car', available: true, unavailable_from: null, equipment_stock: 6 },
    { id: 'E03', name: 'Бригада Комарь', start_lat: 55.73, start_lon: 37.74, shift_start: '10:00', shift_end: '22:00', skills: ['local'], transport: 'public', available: false, unavailable_from: '13:00', equipment_stock: 6 },
  ];
}

function requests(): ServiceRequest[] {
  return [
    // К обеим заявкам на подключение бригада везёт по единице оборудования: роутер или приставку.
    request('74198', 'Город Москва, пр-кт.Волгоградский, д. 128 к 5', 55.7008, 37.7822, 'connection', '10:00', '12:00', 60, {
      needs_equipment: true,
    }),
    request('86160', 'Город Москва, пер.Маяковского, д. 2', 55.7431, 37.6612, 'connection', '12:00', '14:00', 60, {
      needs_equipment: true,
    }),
    request('50104', 'Город Москва, ул.Грайвороновская, д. 10 к 2', 55.7212, 37.7336, 'local', '14:00', '16:00', 45),
    request('46393', 'Город Москва, ул.Шарикоподшипниковская, д. 14', 55.7195, 37.68, 'local', '15:00', '17:00', 45, {
      transport_required: 'car',
    }),
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
    ], lunch('15:55', '16:40')),
    route('E02', [visit('84627', '09:25', '12:00', '12:40', 5.9, 25, true), visit('URG-001', '13:05', '13:05', '14:05', 5.3, 25)], lunch('14:05', '14:50')),
    route('E03', []),
  ];
  const previousRoutes = [
    route('E01', [
      visit('74198', '09:35', '10:00', '11:00', 6.1, 35, true),
      visit('86160', '11:40', '12:00', '13:00', 7.9, 40, true),
      visit('46393', '13:30', '15:00', '15:45', 6.4, 30),
    ], lunch('13:30', '14:15')),
    route('E02', [visit('84627', '09:25', '12:00', '12:40', 5.9, 25, true), visit('50104', '13:05', '14:00', '14:45', 5.4, 25)], lunch('13:05', '13:50')),
    route('E03', []),
  ];
  const baselineRoutes = [
    route('E01', [
      visit('74198', '09:35', '10:00', '11:00', 6.1, 35, true),
      visit('86160', '11:40', '12:00', '13:00', 7.9, 40, true),
      visit('46393', '13:35', '15:00', '15:45', 7.0, 35),
    ], lunch('13:35', '14:20')),
    route('E02', [visit('84627', '09:25', '12:00', '12:40', 5.9, 25, true), visit('URG-001', '13:05', '13:05', '14:05', 5.3, 25)], lunch('14:05', '14:50')),
    route('E03', []),
  ];
  // План диспетчеров показываем как есть, без обеда.
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
    workload_level: 1,
    lunch_enabled: true,
    cursor: '13:00',
    timeline: [],
    timeline_ready: true,
    ...overrides,
  };
}

/** Событие на шкале дня: применённое, впереди или отклонённое. */
export function makeTimelineItem(overrides: Partial<TimelineItem> = {}): TimelineItem {
  return {
    id: 'tl_1',
    event: { type: 'cancel', time: '09:30', request: null, request_id: '10135', engineer_id: null },
    status: 'applied',
    reason: null,
    variant: null,
    choosable: false,
    ...overrides,
  };
}

/** Шкала дня фикстуры: три применённых события плана и два впереди, одно из них отклонено. */
export function makeTimeline(): TimelineItem[] {
  const [cancel, unavailable, urgent] = makePlanningState().events.map((item) => item.event);
  return [
    makeTimelineItem({ id: 'tl_1', event: cancel }),
    makeTimelineItem({ id: 'tl_2', event: unavailable }),
    makeTimelineItem({ id: 'tl_3', event: urgent }),
    makeTimelineItem({ id: 'tl_4', event: makeDelayEvent({ time: '15:00' }), status: 'pending' }),
    makeTimelineItem({
      id: 'tl_5',
      event: { type: 'cancel', time: '16:30', request: null, request_id: '74198', engineer_id: null },
      status: 'rejected',
      reason: 'Заявка 74198 уже выполнена.',
    }),
  ];
}

const BASE_VARIANT_TEXTS: Record<string, { title: string; summary: string }> = {
  optimal: { title: 'Оптимально по дню', summary: 'Пересчитать остаток дня целиком' },
  stable: { title: 'Минимум перестановок', summary: 'Чужие маршруты почти не трогаем' },
  keep: { title: 'Ничего не менять', summary: 'Оставить маршруты как есть' },
};

/**
 * Вариант исправления с итогами плана фикстуры. У «отдать бригаде» имя в заголовке и своя бригада заявки,
 * а сравнивают его всегда с «Оптимально по дню»; три готовые стратегии сравнены с рекомендованной.
 */
export function makeVariantOption(variant: EventVariant, patch: Partial<VariantOption> = {}): VariantOption {
  const assigned = variant.startsWith('assign:') ? variant.slice('assign:'.length) : null;
  const texts = assigned
    ? { title: `Отдать: ${engineers().find((item) => item.id === assigned)?.name ?? assigned}`, summary: 'Выбор диспетчера' }
    : BASE_VARIANT_TEXTS[variant];
  return {
    variant,
    ...texts,
    metrics: makePlanningState().plan.metrics,
    late: 0,
    moved: 0,
    pros: [],
    cons: [],
    recommended: false,
    request_engineer_id: assigned,
    compared_to: variant === 'optimal' ? 'stable' : 'optimal',
    ...patch,
  };
}

/**
 * Варианты исправления для недоступности Бригады Белузин в 13:00: рекомендован пересчёт дня,
 * минимум перестановок держит маршруты, «ничего не менять» оставляет двух клиентов без инженера и двоих с опозданием.
 */
export function makeEventChoice(overrides: Partial<EventChoice> = {}): EventChoice {
  const metrics = makePlanningState().plan.metrics;
  return {
    entry_id: 'tl_2',
    event: { type: 'engineer_unavailable', time: '13:00', request: null, request_id: null, engineer_id: 'E02' },
    metrics_before: metrics,
    late_before: 0,
    variants: [
      makeVariantOption('optimal', {
        recommended: true,
        moved: 3,
        pros: ['на 1 бригаду меньше'],
        cons: ['на 3 заявки больше переезжает к другим бригадам'],
      }),
      makeVariantOption('stable', { moved: 0, pros: ['на 3 заявки меньше переезжает к другим бригадам'], cons: ['на 1 бригаду больше'] }),
      makeVariantOption('keep', {
        late: 2,
        metrics: { ...metrics, unassigned: metrics.unassigned + 2 },
        cons: ['на 4 клиента без инженера или с опозданием больше'],
      }),
    ],
    current: null,
    assignable: false,
    ...overrides,
  };
}

/**
 * Варианты для срочной заявки URG-001 в 13:00: только у неё диспетчер может отдать заявку конкретной бригаде.
 * По оптимальному плану её берёт Бригада Белузин, «ничего не менять» оставляет заявку без бригады.
 */
export function makeUrgentChoice(overrides: Partial<EventChoice> = {}): EventChoice {
  const base = makeEventChoice();
  return {
    ...base,
    entry_id: 'tl_3',
    event: { type: 'urgent', time: '13:00', request: requests()[7], request_id: null, engineer_id: null },
    variants: [
      makeVariantOption('optimal', { recommended: true, moved: 1, request_engineer_id: 'E02', pros: ['срочная заявка без опоздания'] }),
      makeVariantOption('stable', { request_engineer_id: 'E02', cons: ['на 3,0 км больше пробега'] }),
      makeVariantOption('keep', { request_engineer_id: null, cons: ['срочная заявка остаётся без инженера'] }),
    ],
    assignable: true,
    ...overrides,
  };
}

/** Смена транспорта Бригады Арташкин с автомобиля на велосипед, как её возвращает сервер после применения. */
export function makeTransportChangeEvent(overrides: Partial<PlanEvent> = {}): PlanEvent {
  return {
    type: 'engineer_transport_changed',
    time: '13:30',
    request: null,
    request_id: null,
    engineer_id: 'E01',
    transport: 'bike',
    previous_transport: 'car',
    ...overrides,
  };
}

/** Изменение заявки 50104: окно на час позже и визит длиннее, как его возвращает сервер после применения. */
export function makeRequestUpdateEvent(overrides: Partial<PlanEvent> = {}): PlanEvent {
  const previous = requests()[2];
  return {
    type: 'request_updated',
    time: '13:30',
    request: { ...previous, window_start: '15:00', window_end: '17:00', duration_min: 60 },
    request_id: '50104',
    engineer_id: null,
    previous_request: previous,
    ...overrides,
  };
}

/** Переназначение заявки 50104 от Бригады Арташкин к Бригаде Белузин в 13:30, как его возвращает сервер после применения. */
export function makeReassignEvent(overrides: Partial<PlanEvent> = {}): PlanEvent {
  return {
    type: 'request_reassigned',
    time: '13:30',
    request: null,
    request_id: '50104',
    engineer_id: 'E02',
    previous_engineer_id: 'E01',
    ...overrides,
  };
}

/** Срочная заявка «как можно скорее», добавленная в 13:00: сервер поставил окно от времени события до конца смен в 22:00. */
export function makeAsapRequest(overrides: Partial<ServiceRequest> = {}): ServiceRequest {
  return request('URG-002', 'Город Москва, ул.Перовская, д. 42 к 1', 55.751, 37.787, 'emergency', '13:00', '22:00', 60, {
    priority: 'urgent',
    asap: true,
    transport_required: 'car',
    source_type_bk: 'Срочная заявка диспетчера',
    source_type_hd: '',
    ...overrides,
  });
}

/** План с заявкой «как можно скорее» URG-002: Бригада Белузин едет к ней после URG-001 и обедает после неё. */
export function makeAsapState(): PlanningState {
  const state = makePlanningState();
  const routes = state.plan.routes.map((item) =>
    item.engineer_id === 'E02' ? route('E02', [...item.visits, visit('URG-002', '14:25', '14:25', '15:25', 4.2, 20)], lunch('15:25', '16:10')) : item,
  );
  return {
    ...state,
    requests: [...state.requests, makeAsapRequest()],
    plan: { ...state.plan, routes, metrics: metrics(routes, state.plan.unassigned.length) },
  };
}

/**
 * День, в котором срочные заявки пришли с данными: у них обычный номер «Билайна», а не URG-…, как у заявки диспетчера.
 * Диспетчер видит такую заявку как URG-50104, но в событиях и запросах её номер остаётся сырым.
 */
export function makeDataUrgentState(ids: string[] = ['50104']): PlanningState {
  const urgent = new Set(ids);
  const state = makePlanningState();
  return { ...state, requests: state.requests.map((item) => (urgent.has(item.id) ? { ...item, priority: 'urgent' } : item)) };
}

/** Задержка Бригады Арташкин на 150 минут в 13:30, пока она едет к заявке 50104. */
export function makeDelayEvent(overrides: Partial<PlanEvent> = {}): PlanEvent {
  return {
    type: 'engineer_delayed',
    time: '13:30',
    request: null,
    request_id: null,
    engineer_id: 'E01',
    delay_min: 150,
    ...overrides,
  };
}

/**
 * Прогноз для задержки из makeDelayEvent: с опозданием на 150 минут инженер не успевает к окну 50104,
 * поэтому едет от закреплённого визита 86160 не раньше 16:00 и опаздывает к обоим оставшимся клиентам.
 */
export function makeDelayForecast(overrides: Partial<DelayForecast> = {}): DelayForecast {
  return {
    engineer_id: 'E01',
    delay_min: 150,
    late_without_replan: [
      { request_id: '50104', planned_start: '14:00', forecast_start: '16:35', late_min: 35 },
      { request_id: '46393', planned_start: '15:10', forecast_start: '17:45', late_min: 45 },
    ],
    overtime_without_replan_min: 0,
    ...overrides,
  };
}

/** Состояние после применённой задержки: последнее событие и прогноз опозданий в last_diff. */
export function makeDelayedState(forecast: Partial<DelayForecast> = {}): PlanningState {
  const state = makePlanningState();
  return {
    ...state,
    version: 5,
    events: [...state.events, { id: 'ev_4', event: makeDelayEvent(), version: 5 }],
    last_diff: { ...state.last_diff!, delay_forecast: makeDelayForecast(forecast) },
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
    version: 4,
    transport: 'car',
    source: 'osrm',
    legs: [
      { to_request_id: '84627', coordinates: [[37.802, 55.745], [37.803, 55.752], [37.805, 55.76]] },
      { to_request_id: 'URG-001', coordinates: [[37.805, 55.76], [37.809, 55.712]] },
    ],
  };
}
