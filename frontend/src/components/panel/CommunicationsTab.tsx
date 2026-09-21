import { useState, type MouseEvent } from 'react';
import type { PlanEvent } from '../../api/types';
import { callAgreedText, callChangeText, callList, eventsAhead, type CallKind, type CallRow } from '../../lib/communications';
import { requestActionState } from '../../lib/events';
import { brigadeName } from '../../lib/format';
import { assignmentIndex, byId } from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';

/** Метка строки; у переноса времени метки нет: про него всё сказано строкой «было → стало». */
const KIND_BADGES: Record<CallKind, { text: string; className: string } | null> = {
  lost: { text: 'Сегодня не приедем', className: 'badge badge--urgent' },
  added: { text: 'Договориться о времени', className: 'badge badge--warn' },
  moved: null,
};

/** Чего стоит каждая из двух отмен: цену выбора диспетчер видит до клика, а не по новым строкам звонков. */
const REFUSE_HINTS = {
  optimal: 'Остаток дня пересчитаем: визиты других клиентов могут переехать',
  keep: 'Времена остальных визитов останутся как есть, у бригады появится окно',
};

/**
 * Кому звонить после событий дня (ответ организаторов, вопрос 2: новое время клиенту сообщает служба поддержки).
 * Строка сравнивает текущий план с тем, что клиент знает: утренним временем или тем, которое с ним уже согласовали.
 */
export function CommunicationsTab() {
  const state = useAppStore((s) => s.state);
  const agreed = useAppStore((s) => s.agreed);
  const busy = useAppStore((s) => s.busy);
  const clock = useAppStore((s) => s.clock);
  const markAgreed = useAppStore((s) => s.markAgreed);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const selectRequest = useAppStore((s) => s.selectRequest);
  // Заявка, у которой диспетчер открыл «Клиент отказался»: выбор стратегии отмены разворачивается прямо в строке.
  const [refusing, setRefusing] = useState<string | null>(null);
  if (!state) return null;

  const { pending, agreed: settled } = callList(state, agreed, clock);
  const requests = byId(state.requests);
  const engineers = byId(state.engineers);
  const assigned = assignmentIndex(state.plan);
  const brigade = (engineerId: string | null) =>
    engineerId ? brigadeName(engineers.get(engineerId)?.name ?? engineerId) : 'без бригады';
  // Кнопки стоят внутри строки: клик по ним не открывает заявку.
  const handle = (action: () => void) => (event: MouseEvent) => {
    event.stopPropagation();
    action();
  };
  const cancelFor = (event: PlanEvent, variant: 'optimal' | 'keep') => {
    setRefusing(null);
    void applyEvent(event, variant);
  };

  return (
    <div className="calls">
      <div className="tab-toolbar">
        <strong>Кому позвонить</strong>
        <span className="muted">Новое время клиенту сообщает служба поддержки</span>
      </div>
      {eventsAhead(state) && (
        <p className="muted">На шкале есть события впереди часов: что говорить клиентам после них, будет видно, когда часы туда дойдут.</p>
      )}
      {pending.length === 0 ? (
        <p className="empty">{settled.length === 0 ? 'Звонить некому: клиенты знают то же, что в плане.' : 'Все переносы согласованы.'}</p>
      ) : (
        <ul className="call-list">
          {pending.map((row: CallRow) => {
            const request = requests.get(row.requestId);
            if (!request) return null;
            const badge = KIND_BADGES[row.kind];
            const actions = requestActionState(request, assigned.get(row.requestId)?.visit, { busy, clock });
            const moved = row.kind === 'moved' && row.previousEngineerId !== row.engineerId;
            return (
              <li key={row.requestId} className={`call call--${row.severity}`} onClick={() => selectRequest(row.requestId)}>
                <div className="call__head">
                  <strong>{row.label}</strong>
                  {badge && <span className={badge.className}>{badge.text}</span>}
                </div>
                <div className="muted">{row.address}</div>
                <p className="call__change">
                  {callChangeText(row)}
                  {moved && ` · ${brigade(row.previousEngineerId)} → ${brigade(row.engineerId)}`}
                </p>
                <div className="call__actions">
                  <button
                    type="button"
                    className="btn btn-small"
                    disabled={busy}
                    onClick={handle(() => markAgreed(row.requestId))}
                  >
                    ✓ Согласовано
                  </button>
                  <button
                    type="button"
                    className="btn btn-small"
                    disabled={actions.disabled}
                    title={actions.cancelTitle}
                    onClick={handle(() => setRefusing(refusing === row.requestId ? null : row.requestId))}
                  >
                    ✕ Клиент отказался
                  </button>
                </div>
                {refusing === row.requestId && (
                  <div className="call__refuse">
                    <button
                      type="button"
                      className="btn btn-small"
                      disabled={actions.disabled}
                      onClick={handle(() => cancelFor(actions.cancelEvent, 'optimal'))}
                    >
                      Отменить и пересчитать остаток дня
                    </button>
                    <span className="muted call__hint">{REFUSE_HINTS.optimal}</span>
                    <button
                      type="button"
                      className="btn btn-small"
                      disabled={actions.disabled}
                      onClick={handle(() => cancelFor(actions.cancelEvent, 'keep'))}
                    >
                      Отменить, маршруты не трогать
                    </button>
                    <span className="muted call__hint">{REFUSE_HINTS.keep}</span>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {settled.length > 0 && (
        <>
          <h4>Согласовано</h4>
          <ul className="call-list">
            {settled.map((row) => (
              <li key={row.requestId} className="call call--agreed" onClick={() => selectRequest(row.requestId)}>
                <div className="call__head">
                  <strong>{row.label}</strong>
                  <span className="muted">{callAgreedText(row)}</span>
                </div>
                <div className="muted">{row.address}</div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
