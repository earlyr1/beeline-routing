import { REQUEST_CLOCK_LABELS, requestClockStatus } from '../lib/clock';
import { brigadeName, formatKm, requestLabel, requestWindowPhrase, shortAddress, SKILL_LABELS, TRANSPORT_LABELS } from '../lib/format';
import { assignmentIndex, byId } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';
import { BrigadePicker } from './BrigadePicker';
import { EngineerLink } from './EngineerLink';
import { EquipmentBadge } from './EquipmentBadge';
import { RequestActions } from './RequestActions';
import { TierBadge } from './TierBadge';
import { useExplanation } from './useExplanation';
import { WhyButton } from './WhyPanel';

export function ExplanationCard() {
  const datasetId = useAppStore((s) => s.datasetId);
  const state = useAppStore((s) => s.state);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const clock = useAppStore((s) => s.clock);
  const version = state?.version ?? 0;
  const { explanation, error, loading } = useExplanation(datasetId, selectedRequestId, version);

  if (!state || !selectedRequestId) return null;
  const request = state.requests.find((item) => item.id === selectedRequestId);
  const engineers = byId(state.engineers);
  const engineerLink = (engineerId: string | null) =>
    engineerId ? <EngineerLink engineerId={engineerId} name={engineers.get(engineerId)?.name ?? engineerId} /> : '—';
  // Карточка открыта поверх страницы бригады: ссылка возвращает на неё, выбранная бригада остаётся.
  const brigade = selectedEngineerId ? engineers.get(selectedEngineerId) : undefined;
  // Что с заявкой к времени на часах: объяснение всегда о текущем плане, визит берём из него же.
  const clockStatus =
    request && request.status !== 'cancelled'
      ? requestClockStatus(assignmentIndex(state.plan).get(selectedRequestId)?.visit, clock)
      : null;

  return (
    <section className="explanation" aria-label="Объяснение по заявке">
      {brigade && (
        <button type="button" className="link-button explanation__back" onClick={() => selectRequest(null)}>
          {`← ${brigadeName(brigade.name)}`}
        </button>
      )}
      <header className="explanation__head">
        <div>
          <div className="explanation__title">
            <h3>Заявка {requestLabel(selectedRequestId, request?.priority)}</h3>
            {clockStatus && <span className={`badge badge--clock-${clockStatus}`}>{REQUEST_CLOCK_LABELS[clockStatus]}</span>}
          </div>
          {request && (
            <p className="muted">
              {shortAddress(request.address)} · {requestWindowPhrase(request)} ·{' '}
              {request.duration_min} мин · {SKILL_LABELS[request.skill]}
              {request.transport_required ? ` · нужен транспорт «${TRANSPORT_LABELS[request.transport_required]}»` : ''}
              {request.priority === 'urgent' ? ' · срочная' : ''}
              {/* Те же метки, что в строке списка заявок: уровень распределения и единица оборудования. */}
              {request.tier === 'connection' && (
                <>
                  {' '}
                  <TierBadge tier={request.tier} />
                </>
              )}
              {request.needs_equipment && (
                <>
                  {' '}
                  <EquipmentBadge />
                </>
              )}
            </p>
          )}
        </div>
        <div className="explanation__actions">
          {/* Те же кнопки и правила, что в строке списка заявок. */}
          {request && <RequestActions request={request} />}
          <button type="button" className="btn btn-ghost btn-small" onClick={() => selectRequest(null)} aria-label="Закрыть объяснение">
            ✕
          </button>
        </div>
      </header>
      {/* Бригада заявки в текущем плане; выбор другой ставит событие «Переназначение заявки» на время часов.
          Ключ по заявке: открытый список не переезжает в карточку другой заявки. */}
      {request && <BrigadePicker key={request.id} request={request} />}
      {loading && <p className="muted">Загружаем объяснение…</p>}
      {error && <p className="error-text">{error}</p>}
      {explanation && !loading && (
        <>
          {/* На виду короткий вывод; проверки, факторы и другие инженеры — в панели «Почему». */}
          <div className="explanation__verdict">
            <p className="explanation__summary">{explanation.summary}</p>
            <WhyButton />
          </div>
          {explanation.visit && (
            <p className="muted">
              {engineerLink(explanation.engineer_id)}: приезд {explanation.visit.arrival}, начало {explanation.visit.start}, окончание{' '}
              {explanation.visit.end}, в пути {explanation.visit.leg_min} мин ({formatKm(explanation.visit.leg_km)})
            </p>
          )}
          {explanation.unassigned && <p className="warn-text">{explanation.unassigned.reason_text}</p>}
        </>
      )}
    </section>
  );
}
