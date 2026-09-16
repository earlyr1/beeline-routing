import { formatKm, formatSigned } from '../lib/format';
import { displayedPlan } from '../lib/planView';
import { WORKLOAD_LEVELS, clampWorkloadLevel, lunchEnabledOf } from '../lib/workload';
import { useAppStore } from '../store/useAppStore';

/** Смена нагрузки и обеда пересчитывает день с нуля, поэтому оба переключателя предупреждают об этом одинаково. */
const DAY_MODE_TITLE = 'Смена нагрузки или обеда пересчитывает день заново: события дня сбрасываются, часы встают на начало дня';

interface MetricProps {
  label: string;
  value: string;
  delta?: string;
  warn?: boolean;
  title?: string;
}

function Metric({ label, value, delta, warn, title }: MetricProps) {
  return (
    <div className={`metric${warn ? ' metric--warn' : ''}`} title={title}>
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
  const setWorkloadLevel = useAppStore((s) => s.setWorkloadLevel);
  const setLunchEnabled = useAppStore((s) => s.setLunchEnabled);
  const reset = useAppStore((s) => s.reset);
  if (!state) return null;

  const current = displayedPlan(state, showPrevious).metrics;
  const base = state.baseline.metrics;
  // Нагрузка и обед сессии, а не выбор в сторе: переключатели не расходятся с планом на экране.
  const level = clampWorkloadLevel(state.workload_level);
  const lunch = lunchEnabledOf(state.lunch_enabled);
  const locked = busy || clockBusy;

  /** Выбор того же уровня ничего не меняет: ни выбора в сторе, ни запроса к серверу. */
  const chooseLevel = (next: number) => {
    if (next === level) return;
    setWorkloadLevel(next);
    void plan();
  };

  const toggleLunch = () => {
    setLunchEnabled(!lunch);
    void plan();
  };

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
      <div className="metric metric--controls">
        <span className="metric__label">Нагрузка</span>
        <div className="metric__switches">
          <select
            className="metric__select"
            aria-label="Нагрузка инженеров"
            title={DAY_MODE_TITLE}
            value={level}
            disabled={locked}
            onChange={(event) => chooseLevel(Number(event.target.value))}
          >
            {WORKLOAD_LEVELS.map((item) => (
              <option key={item.level} value={item.level}>
                {`${item.emoji} ${item.title}`}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn-small metric__lunch"
            aria-label="Обед по плану"
            aria-pressed={lunch}
            title={DAY_MODE_TITLE}
            disabled={locked}
            onClick={toggleLunch}
          >
            {lunch ? 'с обедом' : 'без обеда'}
          </button>
        </div>
      </div>
      <div className="metrics-strip__actions">
        <button type="button" className="btn" onClick={() => void plan()} disabled={locked}>
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
