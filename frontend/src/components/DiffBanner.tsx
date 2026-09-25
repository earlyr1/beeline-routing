import { useState } from 'react';
import { describeEvent, forecastLines, lateVisitsTitle } from '../lib/events';
import { formatKm } from '../lib/format';
import { byId } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';

export function DiffBanner() {
  const state = useAppStore((s) => s.state);
  const move = useAppStore((s) => s.timelineMove);
  // Пока часы идут или диспетчер тянет ползунок, план меняется на каждом событии: баннер не мигает и не двигает карту.
  const moving = useAppStore((s) => s.playing || s.dragging);
  // Скрытые версии плана: часы возвращаются к прежним планам, и скрытый однажды баннер не появляется снова.
  const [dismissed, setDismissed] = useState<ReadonlySet<number>>(() => new Set());
  if (!state || !state.last_diff || moving || dismissed.has(state.version)) return null;

  const diff = state.last_diff;
  const engineers = byId(state.engineers);
  const requests = byId(state.requests);
  const last = state.events[state.events.length - 1];
  const lastText = last ? describeEvent(last.event, engineers, requests) : null;
  // Изменения всегда относятся к последнему применённому событию; после перехода часов назад заголовок говорит, на какое время план.
  const back = Boolean(move?.back);
  const title = back ? `План на ${state.cursor}` : (lastText ?? 'План перестроен');
  const reordered = diff.reordered_engineers.map((engineerId) => engineers.get(engineerId)?.name ?? engineerId);
  const before = diff.metrics_before;
  const after = diff.metrics_after;
  const shifts = diff.time_shifts.filter((item) => item.delta_min !== 0).length;
  const forecast = diff.delay_forecast ?? null;
  // Изменения — от последнего применённого события шкалы. Если оно прошло с «Ничего не менять», прогноз без
  // перепланирования и есть план: опоздания в нём — факт, а не «опоздали бы».
  const lastApplied = (state.timeline ?? []).filter((item) => item.status === 'applied').at(-1);
  const kept = lastApplied?.variant === 'keep';
  const forecastText = forecast ? forecastLines(forecast, requests, engineers, kept) : [];
  const lateTitle = forecast ? lateVisitsTitle(forecast, requests) : '';
  const forecastWarns = forecast !== null && (forecast.late_without_replan.length > 0 || forecast.overtime_without_replan_min > 0);

  return (
    <div className="diff-banner" role="status">
      <div className="diff-banner__text">
        <strong>{title}</strong>
        {back && lastText && <span>{`Изменения показаны для события: ${lastText}`}</span>}
        {(move?.applied ?? 0) > 1 && <span>{`Применено событий: ${move.applied}, изменения показаны для последнего`}</span>}
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
      <button
        type="button"
        className="btn btn-ghost btn-small"
        onClick={() => setDismissed((previous) => new Set(previous).add(state.version))}
      >
        Скрыть
      </button>
    </div>
  );
}
