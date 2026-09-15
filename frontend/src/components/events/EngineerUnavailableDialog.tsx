import { useState, type FormEvent } from 'react';
import { timeError, unavailableEvent } from '../../lib/events';
import { isValidTime, laterTime } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

export function EngineerUnavailableDialog({ onClose }: { onClose: () => void }) {
  const state = useAppStore((s) => s.state);
  const eventTime = useAppStore((s) => s.eventTime);
  const busy = useAppStore((s) => s.busy);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const now = state?.now ?? '00:00';
  const engineers = (state?.engineers ?? []).filter((engineer) => engineer.available);
  const [engineerId, setEngineerId] = useState(engineers[0]?.id ?? '');
  const [time, setTime] = useState(isValidTime(eventTime) ? laterTime(eventTime, now) : now);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const problem = engineerId ? timeError(time, now) : 'Выберите инженера';
    setError(problem);
    if (problem) return;
    if (await applyEvent(unavailableEvent(engineerId, time))) onClose();
  };

  return (
    <div className="dialog" role="dialog" aria-label="Инженер недоступен">
      <form noValidate onSubmit={(event) => void submit(event)}>
        <h3>Инженер недоступен</h3>
        <label className="field">
          <span>Инженер</span>
          <select value={engineerId} onChange={(event) => setEngineerId(event.target.value)}>
            {engineers.map((engineer) => (
              <option key={engineer.id} value={engineer.id}>
                {engineer.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Недоступен с</span>
          <input type="time" value={time} min={now} onChange={(event) => setTime(event.target.value)} />
        </label>
        <p className="muted">Начатые до этого времени работы останутся за инженером, остальные будут перераспределены.</p>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className="dialog__actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Перепланировать
          </button>
        </div>
      </form>
    </div>
  );
}
