import { useState, type FormEvent } from 'react';
import { busiestEngineerId, timeError, unavailableEvent, visitsFrom } from '../../lib/events';
import { isValidTime, laterTime } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

export function EngineerUnavailableDialog({ onClose }: { onClose: () => void }) {
  const state = useAppStore((s) => s.state);
  const eventTime = useAppStore((s) => s.eventTime);
  const busy = useAppStore((s) => s.busy);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const now = state?.now ?? '00:00';
  const engineers = (state?.engineers ?? []).filter((engineer) => engineer.available);
  const initialTime = isValidTime(eventTime) ? laterTime(eventTime, now) : now;
  const [time, setTime] = useState(initialTime);
  // По умолчанию инженер, у которого больше всего визитов после этого времени: иначе событие ничего не изменит.
  const busiest = (at: string) => (state ? busiestEngineerId(engineers, state.plan, at) : null) ?? '';
  const [engineerId, setEngineerId] = useState(() => busiest(initialTime));
  const [engineerTouched, setEngineerTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const countTime = isValidTime(time) ? time : now;

  const changeTime = (value: string) => {
    setTime(value);
    if (!engineerTouched && isValidTime(value)) setEngineerId(busiest(value));
  };

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
          <select
            value={engineerId}
            onChange={(event) => {
              setEngineerTouched(true);
              setEngineerId(event.target.value);
            }}
          >
            {engineers.map((engineer) => (
              <option key={engineer.id} value={engineer.id}>
                {`${engineer.name} (визитов после ${countTime}: ${state ? visitsFrom(state.plan, engineer.id, countTime) : 0})`}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Недоступен с</span>
          <input type="time" value={time} min={now} onChange={(event) => changeTime(event.target.value)} />
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
