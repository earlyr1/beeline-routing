import { timeError } from '../../lib/events';
import { useAppStore } from '../../store/useAppStore';
import { UrgentRequestDialog } from './UrgentRequestDialog';

/** Панель событий: время события и срочная заявка. События инженера открываются со страницы бригады. */
export function EventToolbar() {
  const state = useAppStore((s) => s.state);
  const eventTime = useAppStore((s) => s.eventTime);
  const setEventTime = useAppStore((s) => s.setEventTime);
  const busy = useAppStore((s) => s.busy);
  const showPrevious = useAppStore((s) => s.showPrevious);
  // Открытый диалог хранит стор: главный экран сдвигает по нему плавающие диалоги, а меню карты открывает его с точкой.
  const urgentOpen = useAppStore((s) => s.toolbarDialog === 'urgent');
  const openDialog = useAppStore((s) => s.openToolbarDialog);
  const closeDialog = useAppStore((s) => s.closeToolbarDialog);
  if (!state) return null;

  const error = timeError(eventTime, state.now);
  return (
    <div className="event-toolbar">
      <label className="field field--inline">
        <span>Время события</span>
        <input type="time" value={eventTime} min={state.now} onChange={(event) => setEventTime(event.target.value)} aria-invalid={error !== null} />
      </label>
      {error && <span className="error-text">{error}</span>}
      <button type="button" className="btn btn-danger" disabled={busy || showPrevious} onClick={() => openDialog('urgent')}>
        Срочная заявка
      </button>
      {urgentOpen && <UrgentRequestDialog onClose={closeDialog} />}
    </div>
  );
}
