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
