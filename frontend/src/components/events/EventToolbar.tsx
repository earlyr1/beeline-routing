import { useState } from 'react';
import { timeError } from '../../lib/events';
import { useAppStore } from '../../store/useAppStore';
import { EngineerUnavailableDialog } from './EngineerUnavailableDialog';
import { TransportChangeDialog } from './TransportChangeDialog';
import { UrgentRequestDialog } from './UrgentRequestDialog';

export function EventToolbar() {
  const state = useAppStore((s) => s.state);
  const eventTime = useAppStore((s) => s.eventTime);
  const setEventTime = useAppStore((s) => s.setEventTime);
  const busy = useAppStore((s) => s.busy);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const startDelay = useAppStore((s) => s.startDelay);
  const [dialog, setDialog] = useState<'urgent' | 'transport' | 'unavailable' | null>(null);
  if (!state) return null;

  const error = timeError(eventTime, state.now);
  return (
    <div className="event-toolbar">
      <label className="field field--inline">
        <span>Время события</span>
        <input type="time" value={eventTime} min={state.now} onChange={(event) => setEventTime(event.target.value)} aria-invalid={error !== null} />
      </label>
      {error && <span className="error-text">{error}</span>}
      <button type="button" className="btn btn-danger" disabled={busy || showPrevious} onClick={() => setDialog('urgent')}>
        Срочная заявка
      </button>
      <button type="button" className="btn" disabled={busy || showPrevious} onClick={() => setDialog('transport')}>
        Смена транспорта
      </button>
      {/* Диалог задержки один на экран: его открывает и карточка маршрута, поэтому он живёт в MainScreen. */}
      <button type="button" className="btn" disabled={busy || showPrevious} onClick={() => startDelay(null)}>
        Задержка
      </button>
      <button type="button" className="btn" disabled={busy || showPrevious} onClick={() => setDialog('unavailable')}>
        Инженер недоступен
      </button>
      {dialog === 'urgent' && <UrgentRequestDialog onClose={() => setDialog(null)} />}
      {dialog === 'transport' && <TransportChangeDialog onClose={() => setDialog(null)} />}
      {dialog === 'unavailable' && <EngineerUnavailableDialog onClose={() => setDialog(null)} />}
    </div>
  );
}
