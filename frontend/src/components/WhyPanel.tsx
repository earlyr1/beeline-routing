import { useEffect, useRef, type ReactNode } from 'react';
import { formatSigned, requestLabelOf } from '../lib/format';
import { byId, displayedPlan, routeSummary } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';
import { EngineerLink } from './EngineerLink';
import { useExplanation } from './useExplanation';

const PANEL_ID = 'why-panel';

/** Кнопка «Почему?» у короткого вывода карточки заявки и страницы бригады: открывает и закрывает панель «Почему». */
export function WhyButton() {
  const whyOpen = useAppStore((s) => s.whyOpen);
  const toggleWhy = useAppStore((s) => s.toggleWhy);
  return (
    <button
      type="button"
      className="btn btn-small why-button"
      aria-pressed={whyOpen}
      // Панели нет в разметке, пока она закрыта: ссылка на неё только у открытой.
      aria-controls={whyOpen ? PANEL_ID : undefined}
      title="Подробное объяснение решения"
      onClick={toggleWhy}
    >
      Почему?
    </button>
  );
}

function Shell({ eyebrow, title, children }: { eyebrow: string; title: string; children: ReactNode }) {
  const closeWhy = useAppStore((s) => s.closeWhy);
  return (
    <aside id={PANEL_ID} className="why-panel" aria-label="Почему">
      <header className="why-panel__head">
        <div>
          <p className="why-panel__eyebrow">{eyebrow}</p>
          <h3 tabIndex={-1} data-why-focus="">
            {title}
          </h3>
        </div>
        <button type="button" className="btn btn-ghost btn-small" aria-label="Закрыть «Почему»" onClick={closeWhy}>
          ✕
        </button>
      </header>
      <div className="why-panel__body">{children}</div>
    </aside>
  );
}

/** Разбор выбора по заявке: проверенные ограничения, факторы и другие инженеры. */
function RequestWhy({ requestId }: { requestId: string }) {
  const datasetId = useAppStore((s) => s.datasetId);
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const { explanation, error, loading } = useExplanation(datasetId, requestId, state?.version ?? 0);
  if (!state) return null;
  const engineers = byId(state.engineers);
  const link = (engineerId: string) => <EngineerLink engineerId={engineerId} name={engineers.get(engineerId)?.name ?? engineerId} />;

  return (
    <Shell eyebrow="Почему так" title={`Заявка ${requestLabelOf(requestId, byId(state.requests))}`}>
      {showPrevious && <p className="note">Объяснение относится к текущему плану, после события.</p>}
      {loading && <p className="muted">Загружаем объяснение…</p>}
      {error && <p className="error-text">{error}</p>}
      {explanation && !loading && (
        <>
          {explanation.engineer_id && <p className="why-panel__lead">Исполнитель: {link(explanation.engineer_id)}</p>}
          {explanation.unassigned && <p className="warn-text">{explanation.unassigned.reason_text}</p>}
          {/* Отменённой заявке и заявке без адреса проверять нечего: вывод говорит сам за себя. */}
          {!explanation.engineer_id && !explanation.unassigned && <p className="why-panel__lead">{explanation.summary}</p>}
          {explanation.constraints.length > 0 && (
            <>
              <h4>Проверки</h4>
              <ul className="checks" aria-label="Проверки">
                {explanation.constraints.map((check) => (
                  <li key={check.name} className={check.ok ? 'check check--ok' : 'check check--fail'}>
                    <span aria-hidden="true">{check.ok ? '✓' : '✗'}</span>
                    <strong>{check.name}</strong>
                    <span>{check.detail}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {explanation.factors.length > 0 && (
            <>
              <h4>Почему такой выбор</h4>
              <ul className="factors" aria-label="Почему такой выбор">
                {explanation.factors.map((factor) => (
                  <li key={factor}>{factor}</li>
                ))}
              </ul>
            </>
          )}
          {explanation.alternatives.length > 0 && (
            <>
              <h4>Другие инженеры</h4>
              {/* Узкая панель: вместо таблицы из пяти колонок по строке на инженера и комментарий под ней. */}
              <ul className="alternatives" aria-label="Другие инженеры">
                {explanation.alternatives.map((alternative) => (
                  <li key={alternative.engineer_id} className="alternative">
                    <div className="alternative__head">
                      {link(alternative.engineer_id)}
                      <span className={alternative.feasible ? 'badge badge--diff' : 'badge'}>
                        {alternative.feasible ? 'может взять' : 'не может'}
                      </span>
                      {alternative.start && <span className="muted">начало {alternative.start}</span>}
                      {alternative.extra_km !== null && (
                        <span className="muted">{`${formatSigned(alternative.extra_km, 1)} км`}</span>
                      )}
                    </div>
                    <p className="alternative__reason muted">{alternative.reason}</p>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </Shell>
  );
}

/** Разбор маршрута бригады: доля пробега, запас до конца окон и смены, закреплённые визиты. */
function RouteWhy({ engineerId }: { engineerId: string }) {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  if (!state) return null;
  const summary = routeSummary(state, displayedPlan(state, showPrevious), engineerId);
  if (!summary) return null;
  return (
    <Shell eyebrow="Почему такой маршрут" title={summary.engineer.name}>
      {showPrevious && state.previous_plan && <p className="note">Маршрут по плану до события.</p>}
      <ul className="factors why-panel__reasons" aria-label="Почему такой маршрут">
        {summary.sentences.map((sentence) => (
          <li key={sentence}>{sentence}</li>
        ))}
      </ul>
    </Shell>
  );
}

function isEditable(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
}

/**
 * Панель «Почему» слева от карты: подробное объяснение того, что открыто справа. Карточка заявки важнее страницы
 * бригады, как и в правой колонке. Закрывается крестиком, Esc или повторным «Почему?».
 */
export function WhyPanel() {
  const whyOpen = useAppStore((s) => s.whyOpen);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const closeWhy = useAppStore((s) => s.closeWhy);
  const wasOpen = useRef(whyOpen);

  useEffect(() => {
    if (!whyOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      // Esc закрывает только верхний слой: меню часов и карты гасят его сами, диалог и поле ввода важнее панели.
      if (event.key !== 'Escape' || event.defaultPrevented || isEditable(event.target)) return;
      const { mapMenu, toolbarDialog, editingRequestId, delayDialogOpen, engineerDialog, choice, choiceLoading } = useAppStore.getState();
      if (mapMenu || toolbarDialog || editingRequestId || delayDialogOpen || engineerDialog || choice || choiceLoading) return;
      closeWhy();
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [whyOpen, closeWhy]);

  // Открытая панель забирает фокус на свой заголовок, закрытая возвращает его кнопке «Почему?», если та на месте.
  useEffect(() => {
    if (whyOpen && !wasOpen.current) document.querySelector<HTMLElement>(`#${PANEL_ID} [data-why-focus]`)?.focus();
    if (!whyOpen && wasOpen.current) document.querySelector<HTMLElement>('.why-button')?.focus();
    wasOpen.current = whyOpen;
  }, [whyOpen]);

  if (!whyOpen) return null;
  if (selectedRequestId) return <RequestWhy key={selectedRequestId} requestId={selectedRequestId} />;
  if (selectedEngineerId) return <RouteWhy engineerId={selectedEngineerId} />;
  return null;
}
