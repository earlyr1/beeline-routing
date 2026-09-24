import { useEffect, useState } from 'react';
import { CANCEL_UNDO_MS, useAppStore } from '../store/useAppStore';

/** Как часто перерисовывается число секунд в уведомлении. */
const COUNTDOWN_TICK_MS = 200;

const isEditable = (target: EventTarget | null) =>
  target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));

/**
 * Уведомление «Заявка … отменена» с отсчётом: защита от мисклика. Пока идёт отсчёт, на сервер ничего не ушло;
 * «✓» отправляет отмену сейчас, «✕» или Esc — отменяет её, на нуле она применяется сама и уведомление пропадает.
 * Живая область стоит на странице всегда: так программа чтения с экрана объявляет уведомление, как только оно появилось.
 */
export function CancelToast() {
  const pending = useAppStore((s) => s.pendingCancel);
  const confirmCancel = useAppStore((s) => s.confirmCancel);
  const undoCancel = useAppStore((s) => s.undoCancel);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!pending) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), COUNTDOWN_TICK_MS);
    return () => clearInterval(timer);
  }, [pending]);

  useEffect(() => {
    if (!pending) return;
    const undoOnEscape = (event: KeyboardEvent) => {
      // Уведомление — самое свежее действие диспетчера, но открытый диалог и поле ввода Esc забирают себе.
      if (event.key !== 'Escape' || event.defaultPrevented || isEditable(event.target)) return;
      const { mapMenu, toolbarDialog, editingRequestId, delayDialogOpen, engineerDialog, choice, choiceLoading, daySummaryOpen } =
        useAppStore.getState();
      if (mapMenu || toolbarDialog || editingRequestId || delayDialogOpen || engineerDialog || choice || choiceLoading || daySummaryOpen) {
        return;
      }
      event.preventDefault();
      undoCancel();
    };
    // Фаза перехвата: Esc не должен заодно закрыть панель «Почему», которая слушает тот же документ.
    document.addEventListener('keydown', undoOnEscape, true);
    return () => document.removeEventListener('keydown', undoOnEscape, true);
  }, [pending, undoCancel]);

  const left = pending ? Math.max(0, pending.deadline - now) : 0;
  const seconds = Math.ceil(left / 1000);
  const message = pending ? `Заявка ${pending.label} отменена` : '';

  return (
    <>
      <div className="visually-hidden" role="status" aria-live="polite">
        {pending && `${message}. Через ${CANCEL_UNDO_MS / 1000} секунд отмена применится, Esc — не отменять.`}
      </div>
      {pending && (
        <div className="toast toast--undo">
          <span className="toast__text">{message}</span>
          <span className="toast__count" aria-hidden="true">
            {seconds}
          </span>
          <button
            type="button"
            className="btn btn-small toast__confirm"
            onClick={() => void confirmCancel()}
            aria-label={`Подтвердить отмену заявки ${pending.label}`}
            title="Подтвердить сейчас"
          >
            ✓
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-small"
            onClick={undoCancel}
            aria-label={`Не отменять заявку ${pending.label}`}
            title="Не отменять (Esc)"
          >
            ✕
          </button>
          {/* Полоса убывает за время отсчёта; key перезапускает её у каждой новой отмены. */}
          <span
            key={pending.deadline}
            className="toast__bar"
            aria-hidden="true"
            style={{ animationDuration: `${CANCEL_UNDO_MS}ms` }}
          />
        </div>
      )}
    </>
  );
}
