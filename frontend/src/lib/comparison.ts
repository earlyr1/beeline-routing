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
