import { useEffect, useState } from 'react';
import { useAppStore } from '../../store/useAppStore';
import { UrgentRequestDialog } from './UrgentRequestDialog';

/**
 * Панель событий: сброс событий и срочная заявка. Время события берётся с часов дня, события инженера открываются
 * со страницы бригады.
 */
export function EventToolbar() {
  const state = useAppStore((s) => s.state);
  const busy = useAppStore((s) => s.busy);
  // Сброс, как и удаление события, меняет план: не во время расчёта, фиксации часов и проигрывания.
  const clockBusy = useAppStore((s) => s.playing || s.committing);
  // Открытый диалог хранит стор: главный экран сдвигает по нему плавающие диалоги, а меню карты открывает его само.
  const urgentOpen = useAppStore((s) => s.toolbarDialog === 'urgent');
  const openDialog = useAppStore((s) => s.openToolbarDialog);
  const closeDialog = useAppStore((s) => s.closeToolbarDialog);
  const resetEvents = useAppStore((s) => s.resetEvents);
  /** Подтверждение сброса открыто: сброс убирает все события шкалы разом. */
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!confirming) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setConfirming(false);
    };
    document.addEventListener('keydown', closeOnEscape, true);
    return () => document.removeEventListener('keydown', closeOnEscape, true);
  }, [confirming]);

  if (!state) return null;
  const hasEvents = (state.timeline ?? []).length > 0;
  const resetLocked = busy || clockBusy || !hasEvents;

  const reset = async () => {
    if (await resetEvents()) setConfirming(false);
  };

  return (
    <div className="event-toolbar">
      <button
        type="button"
        className="btn"
        disabled={resetLocked}
        title={hasEvents ? 'Убрать все события и вернуться к утреннему плану' : 'На шкале нет событий'}
        onClick={() => setConfirming((open) => !open)}
      >
        Сброс событий
      </button>
      <button type="button" className="btn btn-danger" disabled={busy} onClick={() => openDialog('urgent')}>
        Срочная заявка
      </button>
      {confirming && !resetLocked && (
        <div className="dialog event-toolbar__confirm" role="dialog" aria-label="Сброс событий">
          <p>Удалить все события со шкалы и вернуться к утреннему плану? Часы встанут на начало дня.</p>
          <div className="dialog__actions">
            <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)}>
              Отмена
            </button>
            <button type="button" className="btn btn-danger" onClick={() => void reset()}>
              Сбросить
            </button>
          </div>
        </div>
      )}
      {urgentOpen && <UrgentRequestDialog onClose={closeDialog} />}
    </div>
  );
}
