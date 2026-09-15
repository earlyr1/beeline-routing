import { engineerColor } from '../../lib/colors';
import { formatKm, formatWindow, TRANSPORT_LABELS } from '../../lib/format';
import { displayedPlan, engineerIdsOf } from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';

/** Вкладка «Бригады»: все инженеры с транспортом, сменой и нагрузкой в показанном плане. Клик открывает страницу бригады. */
export function BrigadesTab() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const selectEngineer = useAppStore((s) => s.selectEngineer);
  if (!state) return null;

  const plan = displayedPlan(state, showPrevious);
  const ids = engineerIdsOf(state);

  return (
    <ul className="brigade-list">
      {state.engineers.map((engineer) => {
        const route = plan.routes.find((item) => item.engineer_id === engineer.id);
        const visits = route?.visits.length ?? 0;
        const km = route?.total_km ?? 0;
        const selected = engineer.id === selectedEngineerId;
        return (
          <li key={engineer.id}>
            <button
              type="button"
              className={`brigade-row${selected ? ' brigade-row--selected' : ''}`}
              onClick={() => {
                // Страница бригады стоит на месте карточки заявки: открытая карточка закрывается.
                selectRequest(null);
                selectEngineer(engineer.id);
              }}
            >
              <span className="dot" style={{ background: engineerColor(engineer.id, ids) }} />
              <span className="brigade-row__main">
                <span className="brigade-row__title">
                  <strong>{engineer.name}</strong>
                  {!engineer.available && (
                    <span
                      className="badge badge--warn"
                      title={engineer.unavailable_from ? `Недоступен с ${engineer.unavailable_from}` : undefined}
                    >
                      Недоступен
                    </span>
                  )}
                </span>
                <span className="brigade-row__meta">
                  {`${TRANSPORT_LABELS[engineer.transport]} · смена ${formatWindow(engineer.shift_start, engineer.shift_end)} · визитов: ${visits} · ${formatKm(km)}`}
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
