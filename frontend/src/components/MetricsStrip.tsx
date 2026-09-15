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
