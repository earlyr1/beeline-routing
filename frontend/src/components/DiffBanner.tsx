import { useState } from 'react';
import { describeEvent, forecastLines, lateVisitsTitle } from '../lib/events';
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
  const engineers = byId(state.engineers);
  const last = state.events[state.events.length - 1];
  const reordered = diff.reordered_engineers.map((engineerId) => engineers.get(engineerId)?.name ?? engineerId);
  const before = diff.metrics_before;
  const after = diff.metrics_after;
  const shifts = diff.time_shifts.filter((item) => item.delta_min !== 0).length;
  const forecast = diff.delay_forecast ?? null;
  const forecastText = forecast ? forecastLines(forecast, byId(state.requests), engineers) : [];
  const lateTitle = forecast ? lateVisitsTitle(forecast) : '';
  const forecastWarns = forecast !== null && (forecast.late_without_replan.length > 0 || forecast.overtime_without_replan_min > 0);

  return (
    <div className="diff-banner" role="status">
      <div className="diff-banner__text">
        <strong>{last ? describeEvent(last.event, engineers) : 'План перестроен'}</strong>
        <span>
          Новых назначений: {diff.added.length} · перенесено: {diff.moved.length} · снято: {diff.removed.length} · сдвиг
          времени: {shifts}
        </span>
        <span>
          Инженеров {before.engineers_used} → {after.engineers_used} · пробег {formatKm(before.total_km)} →{' '}
          {formatKm(after.total_km)} · не назначено {before.unassigned} → {after.unassigned}
        </span>
        {forecastText.map((line) => (
          <span key={line} className={forecastWarns ? 'warn-text' : undefined} title={lateTitle || undefined}>
            {line}
          </span>
        ))}
        {reordered.length > 0 && <span>{`Изменён порядок: ${reordered.join(', ')}`}</span>}
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
