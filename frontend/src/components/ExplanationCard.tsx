import { useEffect, useState } from 'react';
import { getExplanation } from '../api/client';
import type { Explanation } from '../api/types';
import { brigadeName, formatKm, formatSigned, formatWindow, shortAddress, SKILL_LABELS, TRANSPORT_LABELS } from '../lib/format';
import { byId } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';
import { EngineerLink } from './EngineerLink';
import { RequestActions } from './RequestActions';

export function ExplanationCard() {
  const datasetId = useAppStore((s) => s.datasetId);
  const state = useAppStore((s) => s.state);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const version = state?.version ?? 0;
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!datasetId || !selectedRequestId) {
      setExplanation(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getExplanation(datasetId, selectedRequestId)
      .then((data) => {
        if (!cancelled) setExplanation(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setExplanation(null);
        setError(err instanceof Error ? err.message : 'Не удалось получить объяснение');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId, selectedRequestId, version]);

  if (!state || !selectedRequestId) return null;
  const request = state.requests.find((item) => item.id === selectedRequestId);
  const engineers = byId(state.engineers);
  const engineerLink = (engineerId: string | null) =>
    engineerId ? <EngineerLink engineerId={engineerId} name={engineers.get(engineerId)?.name ?? engineerId} /> : '—';
  // Карточка открыта поверх страницы бригады: ссылка возвращает на неё, выбранная бригада остаётся.
  const brigade = selectedEngineerId ? engineers.get(selectedEngineerId) : undefined;

  return (
    <section className="explanation" aria-label="Объяснение по заявке">
      {brigade && (
        <button type="button" className="link-button explanation__back" onClick={() => selectRequest(null)}>
          {`← ${brigadeName(brigade.name)}`}
        </button>
      )}
      <header className="explanation__head">
        <div>
          <h3>Заявка {selectedRequestId}</h3>
          {request && (
            <p className="muted">
              {shortAddress(request.address)} · окно {formatWindow(request.window_start, request.window_end)} ·{' '}
              {request.duration_min} мин · {SKILL_LABELS[request.skill]}
              {request.transport_required ? ` · нужен транспорт «${TRANSPORT_LABELS[request.transport_required]}»` : ''}
              {request.priority === 'urgent' ? ' · срочная' : ''}
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
      {showPrevious && <p className="note">Объяснение относится к текущему плану, после события.</p>}
      {loading && <p className="muted">Загружаем объяснение…</p>}
      {error && <p className="error-text">{error}</p>}
      {explanation && !loading && (
        <>
          <p className="explanation__summary">{explanation.summary}</p>
          {explanation.visit && (
            <p className="muted">
              {engineerLink(explanation.engineer_id)}: приезд {explanation.visit.arrival}, начало {explanation.visit.start}, окончание{' '}
              {explanation.visit.end}, в пути {explanation.visit.leg_min} мин ({formatKm(explanation.visit.leg_km)})
            </p>
          )}
          {explanation.unassigned && <p className="warn-text">{explanation.unassigned.reason_text}</p>}
          <ul className="checks">
            {explanation.constraints.map((check) => (
              <li key={check.name} className={check.ok ? 'check check--ok' : 'check check--fail'}>
                <span aria-hidden="true">{check.ok ? '✓' : '✗'}</span>
                <strong>{check.name}</strong>
                <span>{check.detail}</span>
              </li>
            ))}
          </ul>
          {explanation.factors.length > 0 && (
            <>
              <h4>Почему такой выбор</h4>
              <ul className="factors">
                {explanation.factors.map((factor) => (
                  <li key={factor}>{factor}</li>
                ))}
              </ul>
            </>
          )}
          {explanation.alternatives.length > 0 && (
            <>
              <h4>Другие инженеры</h4>
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Инженер</th>
                    <th>Может взять</th>
                    <th>Начало</th>
                    <th>Доп. пробег</th>
                    <th>Комментарий</th>
                  </tr>
                </thead>
                <tbody>
                  {explanation.alternatives.map((alternative) => (
                    <tr key={alternative.engineer_id}>
                      <td>{engineerLink(alternative.engineer_id)}</td>
                      <td>{alternative.feasible ? 'да' : 'нет'}</td>
                      <td>{alternative.start ?? '—'}</td>
                      <td>{alternative.extra_km === null ? '—' : `${formatSigned(alternative.extra_km, 1)} км`}</td>
                      <td>{alternative.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </section>
  );
}
