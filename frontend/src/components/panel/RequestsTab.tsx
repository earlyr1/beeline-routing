import type { ServiceRequest } from '../../api/types';
import { REQUEST_CLOCK_LABELS, requestClockStatus } from '../../lib/clock';
import { engineerColor } from '../../lib/colors';
import { capitalize, requestLabel, requestWindowPhrase, shortAddress } from '../../lib/format';
import {
  assignmentIndex,
  byId,
  diffBadge,
  diffMarks,
  displayedPlan,
  engineerIdsOf,
  routeRequestIds,
  sortRequestsForList,
} from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';
import { EngineerLink } from '../EngineerLink';
import { EquipmentBadge } from '../EquipmentBadge';
import { TierBadge } from '../TierBadge';
import { RequestActions } from '../RequestActions';

export function RequestsTab() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const selectEngineer = useAppStore((s) => s.selectEngineer);
  const clock = useAppStore((s) => s.clock);
  if (!state) return null;

  const plan = displayedPlan(state, showPrevious);
  const assignments = assignmentIndex(plan);
  const engineers = byId(state.engineers);
  const requests = byId(state.requests);
  const ids = engineerIdsOf(state);
  // Отметки изменений видны и в плане до события: там бейдж говорит, что произойдёт с заявкой.
  const marks = diffMarks(state.last_diff);
  const rows = selectedEngineerId
    ? routeRequestIds(plan, selectedEngineerId)
        .map((id) => requests.get(id))
        .filter((request): request is ServiceRequest => request !== undefined)
    : sortRequestsForList(state.requests, plan);

  return (
    <div className="requests-tab">
      <div className="tab-toolbar">
        <label className="field field--inline">
          <span>Инженер</span>
          <select value={selectedEngineerId ?? ''} onChange={(event) => selectEngineer(event.target.value || null)}>
            <option value="">Все инженеры</option>
            {state.engineers.map((engineer) => (
              <option key={engineer.id} value={engineer.id}>
                {engineer.name}
                {engineer.available ? '' : ' (недоступен)'}
              </option>
            ))}
          </select>
        </label>
        <span className="muted">Заявок: {rows.length}</span>
      </div>
      <ul className="request-list">
        {rows.map((request) => {
          const info = assignments.get(request.id);
          const engineer = info ? engineers.get(info.engineerId) : undefined;
          const mark = marks.get(request.id);
          const badge = mark ? diffBadge(mark, request.id, state.last_diff, engineers, showPrevious) : null;
          const cancelled = request.status === 'cancelled';
          const pinned = Boolean(info?.visit.pinned);
          // Что с заявкой к времени на часах: у отменённой заявки работ нет.
          const clockStatus = cancelled ? null : requestClockStatus(info?.visit, clock);
          const classes = [
            'request-row',
            request.id === selectedRequestId ? 'request-row--selected' : '',
            cancelled ? 'request-row--cancelled' : '',
            mark ? 'request-row--changed' : '',
          ]
            .filter(Boolean)
            .join(' ');
          return (
            <li key={request.id} className={classes} onClick={() => selectRequest(request.id)}>
              <span className="dot" style={{ background: engineerColor(info?.engineerId, ids) }} />
              <div className="request-row__main">
                <div className="request-row__title">
                  <strong>{requestLabel(request.id, request.priority)}</strong>
                  <span>{shortAddress(request.address)}</span>
                </div>
                <div className="request-row__meta">
                  <span>{capitalize(requestWindowPhrase(request))}</span>
                  <span>{info ? `Начало ${info.visit.start}` : cancelled ? 'Отменена' : 'Не назначена'}</span>
                  <span>{engineer ? <EngineerLink engineerId={engineer.id} name={engineer.name} /> : '—'}</span>
                </div>
                <div className="badges">
                  {clockStatus && <span className={`badge badge--clock-${clockStatus}`}>{REQUEST_CLOCK_LABELS[clockStatus]}</span>}
                  {request.priority === 'urgent' && <span className="badge badge--urgent">Срочная</span>}
                  <TierBadge tier={request.tier} />
                  {request.asap && <span className="badge badge--asap">Как можно скорее</span>}
                  {request.needs_equipment && <EquipmentBadge />}
                  {cancelled && <span className="badge badge--cancelled">Отменена</span>}
                  {pinned && <span className="badge">Закреплена</span>}
                  {badge && (
                    <span className="badge badge--diff" title={badge.title}>
                      {badge.text}
                    </span>
                  )}
                </div>
              </div>
              <div className="request-row__actions">
                <RequestActions request={request} />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
