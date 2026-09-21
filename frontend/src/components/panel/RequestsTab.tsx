import type { ServiceRequest } from '../../api/types';
import { REQUEST_CLOCK_LABELS, requestClockStatus } from '../../lib/clock';
import { engineerColor } from '../../lib/colors';
import { capitalize, REASON_LABELS, requestLabel, requestWindowPhrase, shortAddress, SKILL_LABELS } from '../../lib/format';
import {
  assignmentIndex,
  byId,
  diffBadge,
  diffMarks,
  engineerIdsOf,
  routeRequestIds,
  sortRequestsForList,
  unassignedIndex,
} from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';
import { EngineerLink } from '../EngineerLink';
import { EquipmentBadge } from '../EquipmentBadge';
import { TierBadge } from '../TierBadge';
import { RequestActions } from '../RequestActions';

export function RequestsTab() {
  const state = useAppStore((s) => s.state);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const selectEngineer = useAppStore((s) => s.selectEngineer);
  const unassignedOnly = useAppStore((s) => s.unassignedOnly);
  const setUnassignedOnly = useAppStore((s) => s.setUnassignedOnly);
  const clock = useAppStore((s) => s.clock);
  if (!state) return null;

  const { plan } = state;
  const assignments = assignmentIndex(plan);
  const unassigned = unassignedIndex(plan);
  const engineers = byId(state.engineers);
  const requests = byId(state.requests);
  const ids = engineerIdsOf(state);
  const marks = diffMarks(state.last_diff);
  // Фильтр действует и при открытой странице бригады: её маршрут уже на странице, а под ней видно,
  // какую заявку без бригады можно ей отдать. Маршрут бригады список показывает, пока фильтр снят.
  const brigadeRoute = selectedEngineerId && !unassignedOnly ? selectedEngineerId : null;
  const rows = brigadeRoute
    ? routeRequestIds(plan, brigadeRoute)
        .map((id) => requests.get(id))
        .filter((request): request is ServiceRequest => request !== undefined)
    : sortRequestsForList(state.requests, plan).filter((request) => !unassignedOnly || unassigned.has(request.id));

  return (
    <div className="requests-tab">
      <div className="tab-toolbar">
        <label className="field field--inline">
          <span>Инженер</span>
          {/* Список инженеров выбирает, чей маршрут показать, поэтому снимает фильтр; при фильтре в нём «Все инженеры». */}
          <select
            value={brigadeRoute ?? ''}
            onChange={(event) => {
              setUnassignedOnly(false);
              selectEngineer(event.target.value || null);
            }}
          >
            <option value="">Все инженеры</option>
            {state.engineers.map((engineer) => (
              <option key={engineer.id} value={engineer.id}>
                {engineer.name}
                {engineer.available ? '' : ' (недоступен)'}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="btn btn-small requests-tab__unassigned"
          aria-pressed={unassignedOnly}
          onClick={() => setUnassignedOnly(!unassignedOnly)}
        >
          Без исполнителя
          {unassigned.size > 0 && <span className="requests-tab__count">{unassigned.size}</span>}
        </button>
        <span className="muted">Заявок: {rows.length}</span>
      </div>
      {unassignedOnly && rows.length === 0 && <p className="empty">Все заявки распределены.</p>}
      <ul className="request-list">
        {rows.map((request) => {
          const info = assignments.get(request.id);
          const engineer = info ? engineers.get(info.engineerId) : undefined;
          const reason = unassigned.get(request.id);
          const mark = marks.get(request.id);
          const badge = mark ? diffBadge(mark, request.id, state.last_diff, engineers) : null;
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
                  {/* У заявки без бригады на месте бригады вид работ: по нему видно, кто мог бы её взять. */}
                  <span>
                    {engineer ? <EngineerLink engineerId={engineer.id} name={engineer.name} /> : reason ? SKILL_LABELS[request.skill] : '—'}
                  </span>
                </div>
                <div className="badges">
                  {clockStatus && <span className={`badge badge--clock-${clockStatus}`}>{REQUEST_CLOCK_LABELS[clockStatus]}</span>}
                  {reason && <span className="badge badge--warn">{REASON_LABELS[reason.reason_code]}</span>}
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
                {reason && <p className="request-row__reason">{reason.reason_text}</p>}
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
