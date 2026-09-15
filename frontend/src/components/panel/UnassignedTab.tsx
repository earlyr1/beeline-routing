import { formatWindow, REASON_LABELS, shortAddress, SKILL_LABELS } from '../../lib/format';
import { byId, displayedPlan } from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';

export function UnassignedTab() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectRequest = useAppStore((s) => s.selectRequest);
  if (!state) return null;

  const plan = displayedPlan(state, showPrevious);
  const requests = byId(state.requests);
  if (plan.unassigned.length === 0) return <p className="empty">Все заявки распределены.</p>;

  return (
    <ul className="unassigned-list">
      {plan.unassigned.map((item) => {
        const request = requests.get(item.request_id);
        return (
          <li key={item.request_id} className="unassigned-item" onClick={() => selectRequest(item.request_id)}>
            <div className="unassigned-item__head">
              <strong>{item.request_id}</strong>
              <span className="badge badge--warn">{REASON_LABELS[item.reason_code]}</span>
            </div>
            {request && (
              <div className="muted">
                {shortAddress(request.address)} · окно {formatWindow(request.window_start, request.window_end)} ·{' '}
                {SKILL_LABELS[request.skill]}
              </div>
            )}
            <p>{item.reason_text}</p>
          </li>
        );
      })}
    </ul>
  );
}
