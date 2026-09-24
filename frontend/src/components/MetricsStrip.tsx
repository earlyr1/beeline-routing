import { useState } from 'react';
import type { PrecomputedPlan } from '../api/types';
import { formatComputedAt, formatKm, formatSearchTime, formatSigned } from '../lib/format';
import { WORKLOAD_LEVELS, clampWorkloadLevel, lunchEnabledOf } from '../lib/workload';
import { useAppStore } from '../store/useAppStore';

/** Нагрузка и обед меняются вместе и применяются одним пересчётом дня с нуля. */
const DAY_MODE_TITLE =
  'Нагрузка и обед применяются кнопкой «Применить»: день пересчитывается заново, события дня сбрасываются, часы встают на начало дня';

/** Нагрузка и обед дня: выбор в шапке, который ещё не применён, и то, с чем посчитан план на экране. */
interface DayMode {
  level: number;
  lunch: boolean;
}

/**
 * Пометка у метрик, что утренний план посчитан заранее: без неё мгновенный план после «30 секунд поиска» выглядит
 * фокусом. Пометка называет именно утренний план: после событий метрики рядом — уже пересчёт на месте, а не ночной
 * поиск. Подсказка говорит, откуда он и что события дня пересчитываются на месте.
 */
function PrecomputedNote({ info }: { info: PrecomputedPlan }) {
  const search = formatSearchTime(info.search_minutes);
  const title =
    `Утренний план посчитан заранее ночным расчётом: поиск ${search}, закончен ` +
    `${formatComputedAt(info.computed_at)}. Сервис взял его вместо поиска при загрузке дня. ` +
    'События дня пересчитываются от него на месте, за секунды.';
  return (
    <span className="metrics-strip__origin" title={title}>
      {`Утренний план: ночной поиск ${search}`}
    </span>
  );
}

interface MetricProps {
  label: string;
  value: string;
  delta?: string;
  warn?: boolean;
  title?: string;
}

/** Число полосы метрик: подпись над крупным значением. Так же выглядят числа окна «Итоги дня». */
export function Metric({ label, value, delta, warn, title }: MetricProps) {
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
  const busy = useAppStore((s) => s.busy);
  const clock = useAppStore((s) => s.clock);
  // Пересчёт с нуля ставит часы на начало дня: пока часы идут или план переходит на их время, он недоступен.
  const clockBusy = useAppStore((s) => s.playing || s.committing);
  const plan = useAppStore((s) => s.plan);
  const setWorkloadLevel = useAppStore((s) => s.setWorkloadLevel);
  const setLunchEnabled = useAppStore((s) => s.setLunchEnabled);
  const reset = useAppStore((s) => s.reset);
  // Выбор нагрузки и обеда до «Применить»: так оба меняются одним пересчётом, а не двумя подряд.
  const [draft, setDraft] = useState<DayMode | null>(null);
  if (!state) return null;

  const current = state.plan.metrics;
  const base = state.baseline.metrics;
  // План на экране посчитан с нагрузкой и обедом сессии, а не с выбором в сторе.
  const session: DayMode = { level: clampWorkloadLevel(state.workload_level), lunch: lunchEnabledOf(state.lunch_enabled) };
  // Выбор, совпавший с сессией, применять нечего: переключатели снова показывают план на экране.
  const pending = draft !== null && (draft.level !== session.level || draft.lunch !== session.lunch) ? draft : null;
  const shown = pending ?? session;
  const locked = busy || clockBusy;

  const choose = (next: DayMode) => setDraft(next);

  /** Пересчёт дня с нуля с выбранными в шапке нагрузкой и обедом. */
  const rebuild = () => {
    setWorkloadLevel(shown.level);
    setLunchEnabled(shown.lunch);
    setDraft(null);
    void plan();
  };

  return (
    <div className="metrics-strip">
      <div className="metrics-strip__title">
        <strong>{state.office.title}</strong>
        <span className="muted">Сейчас {clock}</span>
        {state.precomputed && <PrecomputedNote info={state.precomputed} />}
      </div>
      <Metric
        label="Инженеров"
        value={`${current.engineers_used} из ${state.engineers.length}`}
        delta={formatSigned(current.engineers_used - base.engineers_used)}
      />
      <Metric label="Пробег" value={formatKm(current.total_km)} delta={`${formatSigned(current.total_km - base.total_km, 1)} км`} />
      <Metric label="Назначено" value={String(current.assigned)} />
      <Metric label="Не назначено" value={String(current.unassigned)} warn={current.unassigned > 0} />
      <div className="metric metric--controls">
        <span className="metric__label">Нагрузка</span>
        <div className="metric__switches">
          <select
            className="metric__select"
            aria-label="Нагрузка инженеров"
            title={DAY_MODE_TITLE}
            value={shown.level}
            disabled={locked}
            onChange={(event) => choose({ ...shown, level: Number(event.target.value) })}
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
            aria-pressed={shown.lunch}
            title={DAY_MODE_TITLE}
            disabled={locked}
            onClick={() => choose({ ...shown, lunch: !shown.lunch })}
          >
            {shown.lunch ? 'с обедом' : 'без обеда'}
          </button>
          {pending && (
            <>
              <button type="button" className="btn btn-small btn-primary" title={DAY_MODE_TITLE} disabled={locked} onClick={rebuild}>
                Применить
              </button>
              <button type="button" className="btn btn-small btn-ghost" disabled={locked} onClick={() => setDraft(null)}>
                Отмена
              </button>
            </>
          )}
        </div>
      </div>
      <div className="metrics-strip__actions">
        <button
          type="button"
          className="btn btn-ghost"
          onClick={reset}
          disabled={busy}
          // День открывают и файлом, и кнопкой региона: после кнопки никакого файла в сессии нет.
          title={busy ? 'Дождитесь окончания расчёта' : 'Файл или день региона'}
        >
          Другие данные
        </button>
      </div>
    </div>
  );
}
