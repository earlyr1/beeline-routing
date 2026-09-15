import { useEffect, useState } from 'react';
import { getExplanation } from '../api/client';
import type { Explanation } from '../api/types';
import { isWorkStarted } from '../lib/events';
import { formatKm, formatSigned, formatWindow, shortAddress, SKILL_LABELS, TRANSPORT_LABELS } from '../lib/format';
import { assignmentIndex, byId } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';

export function ExplanationCard() {
  const datasetId = useAppStore((s) => s.datasetId);
  const state = useAppStore((s) => s.state);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const busy = useAppStore((s) => s.busy);
  const startEdit = useAppStore((s) => s.startEdit);
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
  const nameOf = (engineerId: string | null) => (engineerId ? (engineers.get(engineerId)?.name ?? engineerId) : '—');
  // Те же правила, что у кнопок в списке заявок; начатую работу проверяем по текущему плану.
  const started = request ? isWorkStarted(request, assignmentIndex(state.plan).get(request.id)?.visit) : false;

  return (
    <section className="explanation" aria-label="Объяснение по заявке">
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
          {request && (
            <button
              type="button"
              className="btn btn-small"
              disabled={busy || showPrevious || started}
              title={started ? 'Работа уже началась, изменить нельзя' : undefined}
              onClick={() => startEdit(request.id)}
            >
              Изменить
            </button>
          )}
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
              {nameOf(explanation.engineer_id)}: приезд {explanation.visit.arrival}, начало {explanation.visit.start}, окончание{' '}
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
                      <td>{nameOf(alternative.engineer_id)}</td>
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
