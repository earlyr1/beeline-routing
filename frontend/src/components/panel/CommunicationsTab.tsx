import { useState, type MouseEvent } from 'react';
import type { PlanEvent } from '../../api/types';
import { callAgreedText, callChangeText, callList, eventsAhead, type CallKind, type CallRow } from '../../lib/communications';
import { isWorkStarted, requestActionState } from '../../lib/events';
import { brigadeName } from '../../lib/format';
import { assignmentIndex, byId } from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';

/** Чего стоит каждая из двух отмен: цену выбора диспетчер видит до клика, а не по новым строкам звонков. */
const REFUSE_HINTS = {
  optimal: 'Остаток дня пересчитаем: визиты других клиентов могут переехать',
  keep: 'Времена остальных визитов останутся как есть, у бригады появится окно',
};

/** Повод для звонка словом: цветную полоску слева на проекторе не разглядеть, а пометку видно. */
const CALL_BADGES: Record<CallKind, { text: string; className: string }> = {
  lost: { text: 'Сегодня не приедем', className: 'badge badge--urgent' },
  outside: { text: 'Вне окна', className: 'badge badge--urgent' },
  window: { text: 'Новое окно', className: 'badge badge--warn' },
};

/**
 * Кому звонить после событий дня (ответ организаторов, вопрос 2: новое время клиенту сообщает служба поддержки).
 * Клиент знает окно, а не минуту, поэтому строка появляется, только когда обещанное окно не выполняется:
 * сегодня не приедем, не попадаем в окно или у заявки теперь другое окно. Бригада в строке — справка.
 * «✓ Согласовано» ставит на шкалу событие «Коммуникация» во время часов: названное окно становится окном заявки,
 * «сегодня не приедем» переносит её. «Снять отметку» удаляет это событие.
 */
export function CommunicationsTab() {
  const state = useAppStore((s) => s.state);
  const agreed = useAppStore((s) => s.agreed);
  const busy = useAppStore((s) => s.busy);
  const clock = useAppStore((s) => s.clock);
  const markAgreed = useAppStore((s) => s.markAgreed);
  const deleteTimelineEvent = useAppStore((s) => s.deleteTimelineEvent);
  const cancelRequest = useAppStore((s) => s.cancelRequest);
  const selectRequest = useAppStore((s) => s.selectRequest);
  // Сетка окон визита от сервера: окно, которое диспетчер назовёт клиенту, — её слот.
  const grid = useAppStore((s) => s.config?.window_grid) ?? [];
  // Заявка, у которой диспетчер открыл «Клиент отказался»: выбор стратегии отмены разворачивается прямо в строке.
  const [refusing, setRefusing] = useState<string | null>(null);
  if (!state) return null;

  const { pending, agreed: settled } = callList(state, agreed, clock, grid);
  const requests = byId(state.requests);
  const engineers = byId(state.engineers);
  const assigned = assignmentIndex(state.plan);
  const brigade = (engineerId: string) => brigadeName(engineers.get(engineerId)?.name ?? engineerId);
  // Кнопки стоят внутри строки: клик по ним не открывает заявку.
  const handle = (action: () => void) => (event: MouseEvent) => {
    event.stopPropagation();
    action();
  };
  // Выбор «пересчитать / не трогать» едет в событие как есть; на сервер оно уйдёт после уведомления об отмене.
  const cancelFor = (event: PlanEvent, variant: 'optimal' | 'keep') => {
    setRefusing(null);
    cancelRequest(event, variant);
  };

  return (
    <div className="calls">
      <div className="tab-toolbar">
        <strong>Кому позвонить</strong>
        <span className="muted">Новое окно клиенту сообщает служба поддержки</span>
      </div>
      {eventsAhead(state) && (
        <p className="muted">На шкале есть события впереди часов: что говорить клиентам после них, будет видно, когда часы туда дойдут.</p>
      )}
      {pending.length === 0 ? (
        <p className="empty">Звонить некому: обещанные клиентам окна выполняются.</p>
      ) : (
        <ul className="call-list">
          {pending.map((row: CallRow) => {
            const request = requests.get(row.requestId);
            if (!request) return null;
            const visit = assigned.get(row.requestId)?.visit;
            const actions = requestActionState(request, visit, { busy, clock });
            const started = isWorkStarted(request, visit, clock);
            return (
              <li key={row.requestId} className={`call call--${row.severity}`} onClick={() => selectRequest(row.requestId)}>
                <div className="call__head">
                  <strong>{row.label}</strong>
                  <span className={CALL_BADGES[row.kind].className}>{CALL_BADGES[row.kind].text}</span>
                </div>
                <div className="muted">{row.address}</div>
                <p className="call__change">
                  {callChangeText(row)}
                  {row.engineerId !== null && ` · ${brigade(row.engineerId)}`}
                </p>
                <div className="call__actions">
                  {/* Отметка — событие шкалы: оно меняет окно заявки или переносит её. У работы, которая уже идёт,
                      сервер его не примет: клиенту говорят, что бригада у него, и строка уйдёт с концом визита. */}
                  <button
                    type="button"
                    className="btn btn-small"
                    disabled={busy || started}
                    title={started ? 'Работа уже началась, отметить звонок нельзя' : undefined}
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
                {/* Отметка — событие «Коммуникация» на шкале: снять её — удалить событие, и заявка вернётся к прежнему. */}
                <div className="call__actions">
                  <button
                    type="button"
                    className="btn btn-small"
                    disabled={busy || row.entryId === null}
                    onClick={handle(() => {
                      if (row.entryId !== null) void deleteTimelineEvent(row.entryId);
                    })}
                  >
                    Снять отметку
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
