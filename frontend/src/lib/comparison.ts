import type { Metrics, PlanningState } from '../api/types';
import { formatKm, formatSigned } from './format';

export type MetricKey = 'engineers_used' | 'total_km' | 'assigned' | 'unassigned' | 'violations';

export interface MetricSpec {
  key: MetricKey;
  label: string;
  higherIsBetter: boolean;
  km: boolean;
}

/** Показатели плана, как их подписывает вкладка «Сравнение»; те же подписи у окна «Итоги дня». */
export const METRIC_SPECS: MetricSpec[] = [
  { key: 'engineers_used', label: 'Задействовано инженеров', higherIsBetter: false, km: false },
  { key: 'total_km', label: 'Суммарный пробег', higherIsBetter: false, km: true },
  { key: 'assigned', label: 'Назначено заявок', higherIsBetter: true, km: false },
  { key: 'unassigned', label: 'Не назначено', higherIsBetter: false, km: false },
  { key: 'violations', label: 'Нарушений ограничений', higherIsBetter: false, km: false },
];

export type Verdict = 'better' | 'worse' | 'same';

/** Лучше или хуже: разница округляется так же, как её подпись, и «+0,0 км» не красится. */
export function verdictOf(diff: number, spec: Pick<MetricSpec, 'higherIsBetter' | 'km'>): Verdict {
  const rounded = Number(diff.toFixed(spec.km ? 1 : 0));
  return rounded === 0 ? 'same' : rounded > 0 === spec.higherIsBetter ? 'better' : 'worse';
}

/** Разница словами: «+2» или «−2,7 км». */
export function deltaText(diff: number, spec: Pick<MetricSpec, 'km'>): string {
  return spec.km ? `${formatSigned(diff, 1)} км` : formatSigned(diff);
}

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
  return METRIC_SPECS.map((spec) => {
    const diff = state.plan.metrics[spec.key] - state.baseline.metrics[spec.key];
    return {
      label: spec.label,
      baseline: cell(state.baseline.metrics, spec),
      optimized: cell(state.plan.metrics, spec),
      dispatchers: cell(state.control?.metrics ?? null, spec),
      delta: deltaText(diff, spec),
      verdict: verdictOf(diff, spec),
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
