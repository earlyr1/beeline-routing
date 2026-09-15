import { formatKm, formatSigned } from '../lib/format';
import { displayedPlan } from '../lib/planView';
import { dayModeText, lunchEnabledOf } from '../lib/workload';
import { useAppStore } from '../store/useAppStore';

interface MetricProps {
  label: string;
  value: string;
  delta?: string;
  warn?: boolean;
  /** Мелкое значение для текстовых показателей, которые не должны спорить с числами. */
  compact?: boolean;
  title?: string;
}

function Metric({ label, value, delta, warn, compact, title }: MetricProps) {
  return (
    <div className={`metric${warn ? ' metric--warn' : ''}${compact ? ' metric--compact' : ''}`} title={title}>
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
  const clock = useAppStore((s) => s.clock);
  // Пересчёт с нуля ставит часы на начало дня: пока часы идут или план переходит на их время, он недоступен.
  const clockBusy = useAppStore((s) => s.playing || s.committing);
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
          Сейчас {clock}
          {showPrevious ? ' · показан план до события' : ''}
        </span>
      </div>
      {/* В плане до события базовый вариант уже пересчитан после него: разница с ним ничего не значит. */}
      <Metric
        label="Инженеров"
        value={`${current.engineers_used} из ${state.engineers.length}`}
        delta={showPrevious ? undefined : formatSigned(current.engineers_used - base.engineers_used)}
      />
      <Metric
        label="Пробег"
        value={formatKm(current.total_km)}
        delta={showPrevious ? undefined : `${formatSigned(current.total_km - base.total_km, 1)} км`}
      />
      <Metric label="Назначено" value={String(current.assigned)} />
      <Metric label="Не назначено" value={String(current.unassigned)} warn={current.unassigned > 0} />
      <Metric
        label="Нагрузка"
        value={dayModeText(state.workload_level, lunchEnabledOf(state.lunch_enabled))}
        compact
        title="Стоимость нового инженера для оптимизатора зависит от нагрузки дня"
      />
      <div className="metrics-strip__actions">
        <button type="button" className="btn" onClick={() => void plan()} disabled={busy || clockBusy}>
          Пересчитать с нуля
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={reset}
          disabled={busy}
          title={busy ? 'Дождитесь окончания расчёта' : undefined}
        >
          Другой файл
        </button>
      </div>
    </div>
  );
}
