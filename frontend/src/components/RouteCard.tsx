import { formatDuration, formatKm, formatWindow, SKILL_LABELS, TRANSPORT_LABELS } from '../lib/format';
import { displayedPlan, routeSummary, type RouteStop } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';

function slackText(stop: RouteStop): string {
  if (stop.slackMin === null) return '—';
  return stop.slackMin < 0 ? `опоздание ${-stop.slackMin} мин` : `${stop.slackMin} мин`;
}

/** Карточка маршрута выбранного инженера: визиты по порядку, итоги и объяснение, почему маршрут такой. */
export function RouteCard() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectEngineer = useAppStore((s) => s.selectEngineer);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const busy = useAppStore((s) => s.busy);
  const startDelay = useAppStore((s) => s.startDelay);
  if (!state || !selectedEngineerId || selectedRequestId) return null;
  const summary = routeSummary(state, displayedPlan(state, showPrevious), selectedEngineerId);
  if (!summary) return null;

  const { engineer, stops } = summary;
  const availability = engineer.available ? null : engineer.unavailable_from ? `недоступен с ${engineer.unavailable_from}` : 'недоступен';
  const profile = [
    engineer.skills.map((skill) => SKILL_LABELS[skill]).join(', '),
    TRANSPORT_LABELS[engineer.transport],
    `смена ${formatWindow(engineer.shift_start, engineer.shift_end)}`,
    availability,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <section className="explanation route-card" aria-label="Маршрут инженера">
      <header className="explanation__head">
        <div>
          <h3>{engineer.name}</h3>
          <p className="muted">{profile}</p>
        </div>
        <div className="explanation__actions">
          <button
            type="button"
            className="btn btn-small"
            disabled={busy || showPrevious || !engineer.available}
            title={engineer.available ? undefined : 'Инженер недоступен, задержку поставить нельзя'}
            onClick={() => startDelay(engineer.id)}
          >
            Задержка
          </button>
          <button type="button" className="btn btn-ghost btn-small" onClick={() => selectEngineer(null)} aria-label="Закрыть маршрут">
            ✕
          </button>
        </div>
      </header>
      {showPrevious && state.previous_plan && <p className="note">Маршрут по плану до события.</p>}
      {stops.length > 0 && (
        <p className="muted">
          {`Визитов: ${stops.length} · пробег ${formatKm(summary.totalKm)} · в пути ${formatDuration(summary.travelMin)} · окончание работ ${summary.endOfWork}, конец смены ${engineer.shift_end}`}
        </p>
      )}
      <h4>Почему такой маршрут</h4>
      <ul className="factors">
        {summary.sentences.map((sentence) => (
          <li key={sentence}>{sentence}</li>
        ))}
      </ul>
      {stops.length > 0 && (
        <table className="table table--compact">
          <thead>
            <tr>
              <th>№</th>
              <th>Заявка</th>
              <th>Окно</th>
              <th>Приезд</th>
              <th>Начало</th>
              <th title="Сколько минут остаётся от начала работ до конца окна">Запас</th>
              <th title="Пробег от предыдущей точки">Путь</th>
            </tr>
          </thead>
          <tbody>
            {stops.map((stop) => (
              <tr key={stop.visit.request_id} className="route-card__row" onClick={() => selectRequest(stop.visit.request_id)}>
                <td>{stop.order}</td>
                <td>
                  <strong>{stop.visit.request_id}</strong>
                  {stop.visit.pinned && <span className="badge">Закреплена</span>}
                </td>
                <td>{stop.request ? formatWindow(stop.request.window_start, stop.request.window_end) : '—'}</td>
                <td>{stop.visit.arrival}</td>
                <td>{stop.visit.start}</td>
                <td className={stop.slackMin !== null && stop.slackMin < 0 ? 'warn-text' : undefined}>{slackText(stop)}</td>
                <td>{formatKm(stop.visit.leg_km)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
