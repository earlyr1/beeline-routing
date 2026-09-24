import type { EventType, HHMM, Metrics, PlanningState, TimeSlot } from '../api/types';
import { callList, type AgreedMarks } from './communications';
import { deltaText, METRIC_SPECS, verdictOf, type MetricKey, type MetricSpec, type Verdict } from './comparison';
import { formatKm, isValidTime, toMinutes } from './format';
import { sliderRange } from './timeBar';
import { dayScale } from './timeline';

/** Часы дошли до конца шкалы дня — до максимума ползунка: дальше они не идут. */
export function clockAtDayEnd(state: PlanningState, clock: HHMM): boolean {
  return isValidTime(clock) && toMinutes(clock) >= sliderRange(dayScale(state)).max;
}

/**
 * День закончился: часы в конце шкалы, и план на сервере уже на этом времени. Время, которое стоит на событии
 * в ожидании выбора варианта, до конца не дошло: сначала выбор, потом итоги.
 */
export function dayOver(state: PlanningState, clock: HHMM): boolean {
  return clockAtDayEnd(state, clock) && clockAtDayEnd(state, state.cursor ?? '') && !state.pending_choice;
}

/** Строка таблицы «Утро → итог дня». */
export interface SummaryRow {
  label: string;
  /** Значение утреннего плана; «—» — утром такого нет или сервер его не прислал. */
  morning: string;
  day: string;
  /** Итог минус утро; пусто, когда утра нет. */
  delta: string;
  verdict: Verdict;
  /** Уточнение предыдущей строки: «из них …». */
  nested: boolean;
}

/** Одно число сравнения итогового плана с другим планом. */
export interface SummaryDelta {
  text: string;
  verdict: Verdict;
}

/** Итоговый план против базового FCFS или против диспетчеров. */
export interface SummaryVersus {
  /** С кем сравниваем, в дательном падеже: «к базовому (FCFS)». */
  against: string;
  /** Оговорка к сравнению; null — без оговорок. */
  note: string | null;
  deltas: SummaryDelta[];
}

/** Вид события дня в итогах: несколько типов событий бригады считаются вместе. */
export type SummaryEventKind = 'urgent' | 'cancel' | 'edit' | 'reassign' | 'brigade' | 'call';

export const SUMMARY_EVENT_LABELS: Record<SummaryEventKind, string> = {
  urgent: 'Срочные',
  cancel: 'Отмены',
  edit: 'Правки заявок',
  reassign: 'Переназначения',
  brigade: 'События бригад',
  call: 'Звонки',
};

const KIND_OF: Record<EventType, SummaryEventKind> = {
  urgent: 'urgent',
  cancel: 'cancel',
  // Возврат отменённой заявки интерфейс больше не ставит, но в сохранённых днях он есть: это тоже правка заявки.
  restore: 'edit',
  request_updated: 'edit',
  request_reassigned: 'reassign',
  engineer_unavailable: 'brigade',
  engineer_transport_changed: 'brigade',
  engineer_delayed: 'brigade',
  client_agreed: 'call',
};

export interface SummaryEvents {
  /** Применённые события шкалы: отклонённые и оставшиеся впереди в итоги не входят. */
  total: number;
  kinds: { kind: SummaryEventKind; label: string; count: number }[];
  /** Вариант выбрал диспетчер: в окне выбора или сразу, как у отмены из «Коммуникаций». */
  chosen: number;
  /** Прошли без окна выбора: «Ничего не менять» ломало план не больше пересчёта, выбирать было не из чего. */
  auto: number;
}

export interface SummaryCalls {
  /** Клиенты, с которыми договорились по телефону: назвали окно или сказали, что сегодня не приедем. */
  agreed: number;
  /** Клиенты, которые к концу дня ещё ждут звонка: то же число, что на вкладке «Коммуникации». */
  waiting: number;
}

export interface DaySummary {
  /** Время, на котором закончился день. */
  end: HHMM;
  rows: SummaryRow[];
  /** Итоговый план к базовому и к диспетчерам; пусто — сравнивать не с чем. */
  versus: SummaryVersus[];
  events: SummaryEvents;
  calls: SummaryCalls;
}

const DASH = '—';

const late = (state: PlanningState) =>
  state.plan.routes.reduce((sum, route) => sum + route.visits.filter((visit) => visit.late_min > 0).length, 0);

/** Опоздания утреннего плана: визит начинается позже конца окна — так же их считает сервер (late_min). */
const morningLate = (state: PlanningState) =>
  (state.morning ?? []).filter((item) => item.start !== null && toMinutes(item.start) > toMinutes(item.window_end)).length;

/** Визиты итогового плана, которые закончились к времени на часах. */
function done(state: PlanningState, clock: HHMM): number {
  const now = toMinutes(clock);
  return state.plan.routes.reduce((sum, route) => sum + route.visits.filter((visit) => toMinutes(visit.end) <= now).length, 0);
}

const valueText = (value: number, spec: Pick<MetricSpec, 'km'>) => (spec.km ? formatKm(value) : String(value));

/** Строка «утро → итог» с разницей; без утра разница не считается. */
function row(label: string, morning: number | null, day: number, spec: Omit<MetricSpec, 'key' | 'label'>, nested = false): SummaryRow {
  const diff = morning === null ? null : day - morning;
  return {
    label,
    morning: morning === null ? DASH : valueText(morning, spec),
    day: valueText(day, spec),
    delta: diff === null ? '' : deltaText(diff, spec),
    verdict: diff === null ? 'same' : verdictOf(diff, spec),
    nested,
  };
}

const COUNT = { higherIsBetter: false, km: false };

const specOf = (key: MetricKey): MetricSpec => METRIC_SPECS.find((item) => item.key === key) as MetricSpec;

function rows(state: PlanningState, clock: HHMM): SummaryRow[] {
  const morning: Metrics | null = state.morning_metrics ?? null;
  const metric = (key: MetricKey) => row(specOf(key).label, morning?.[key] ?? null, state.plan.metrics[key], specOf(key));
  // Перенесённые заявки стоят в «Не назначено» вместе с остальными: клиенту сказали, что сегодня не приедем.
  // Утром звонков ещё не было, поэтому и переносов нет.
  const postponed = state.requests.filter((request) => request.status === 'postponed').length;
  return [
    metric('engineers_used'),
    metric('total_km'),
    metric('assigned'),
    { label: `Выполнено к ${clock}`, morning: DASH, day: String(done(state, clock)), delta: '', verdict: 'same', nested: false },
    metric('unassigned'),
    // Уточнение строки выше, а не отдельный показатель: разница не красится.
    { ...row('из них перенесено со звонком клиенту', 0, postponed, COUNT, true), verdict: 'same' },
    metric('violations'),
    // Опоздания утра видны и без итогов сервера: начало утреннего визита и окно заявки лежат в morning.
    row('Опоздания', (state.morning ?? []).length > 0 ? morningLate(state) : null, late(state), COUNT),
  ];
}

/** Что сравнивается с другим планом и как оно подписано: как разница «… к базовому» в полосе метрик. */
const VERSUS_NAMES: [MetricKey, string][] = [
  ['engineers_used', 'инженеров'],
  ['total_km', 'пробег'],
  ['unassigned', 'не назначено'],
];

/** Итоговый план к другому плану: инженеры, пробег и заявки без инженера. */
function deltas(plan: Metrics, other: Metrics): SummaryDelta[] {
  return VERSUS_NAMES.map(([key, name]) => {
    const diff = plan[key] - other[key];
    return { text: `${name} ${deltaText(diff, specOf(key))}`, verdict: verdictOf(diff, specOf(key)) };
  });
}

/**
 * Сравнение итогового плана с базовым FCFS и с диспетчерами, если план диспетчеров есть. Подписи — как во вкладке
 * «Сравнение»: в сгенерированном регионе «диспетчеры» — наша эвристика, а не решения людей, и их день — исходный,
 * без событий.
 */
function versus(state: PlanningState): SummaryVersus[] {
  const result: SummaryVersus[] = [{ against: 'к базовому (FCFS)', note: null, deltas: deltas(state.plan.metrics, state.baseline.metrics) }];
  if (state.control) {
    const notes = [
      ...(state.generated ? ['не решения людей: регион сгенерирован нами, заявки разложила простая эвристика'] : []),
      ...((state.events ?? []).length > 0 ? ['исходный день, события в нём не учтены'] : []),
    ];
    result.push({
      against: state.generated ? 'к диспетчерам' : 'к диспетчерам Билайна',
      note: notes.length > 0 ? notes.join('; ') : null,
      deltas: deltas(state.plan.metrics, state.control.metrics),
    });
  }
  return result;
}

function events(state: PlanningState): SummaryEvents {
  const applied = (state.timeline ?? []).filter((item) => item.status === 'applied');
  const kinds = (Object.keys(SUMMARY_EVENT_LABELS) as SummaryEventKind[]).map((kind) => ({
    kind,
    label: SUMMARY_EVENT_LABELS[kind],
    count: applied.filter((item) => KIND_OF[item.event.type] === kind).length,
  }));
  return {
    total: applied.length,
    kinds,
    chosen: applied.filter((item) => item.variant !== null && !item.variant_auto).length,
    auto: applied.filter((item) => item.variant_auto).length,
  };
}

/**
 * Итоги дня по плану на часах: утро против итога, сравнение с базовым и диспетчерами, события и звонки.
 * Всё берётся из состояния, которое уже на экране: утро — итоги утреннего плана и визиты начала дня (morning),
 * события — применённые события шкалы, звонки — отметки вкладки «Коммуникации» и её же список звонков.
 */
export function daySummary(state: PlanningState, agreed: AgreedMarks, clock: HHMM, grid: TimeSlot[] = []): DaySummary {
  return {
    end: clock,
    rows: rows(state, clock),
    versus: versus(state),
    events: events(state),
    calls: { agreed: Object.keys(agreed).length, waiting: callList(state, agreed, clock, grid).pending.length },
  };
}
