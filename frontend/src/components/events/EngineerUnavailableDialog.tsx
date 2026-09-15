import { useState, type FormEvent } from 'react';
import type { PlanningState } from '../../api/types';
import { busiestEngineerId, effectiveEventTime, timeError, unavailableEvent, visitsFrom } from '../../lib/events';
import { isValidTime } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

/** Диалог «Инженер недоступен»: один на экран, открывается со страницы бригады для её инженера. */
export function EngineerUnavailableDialog() {
  const state = useAppStore((s) => s.state);
  const dialog = useAppStore((s) => s.engineerDialog);
  if (!state || dialog?.kind !== 'unavailable') return null;
  // Ключ по инженеру: кнопка на странице другой бригады заполняет форму заново.
  return <UnavailableForm key={dialog.engineerId} state={state} chosenEngineerId={dialog.engineerId} />;
}

function UnavailableForm({ state, chosenEngineerId }: { state: PlanningState; chosenEngineerId: string }) {
  const eventTime = useAppStore((s) => s.eventTime);
  const busy = useAppStore((s) => s.busy);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const closeEngineerDialog = useAppStore((s) => s.closeEngineerDialog);
  const { now } = state;
  const engineers = state.engineers.filter((engineer) => engineer.available);
  const initialTime = effectiveEventTime(eventTime, now);
  const [time, setTime] = useState(initialTime);
  // Инженер бригады мог уже стать недоступным: тогда предлагаем того, у кого больше всего визитов после этого времени.
  const busiest = (at: string) => busiestEngineerId(engineers, state.plan, at) ?? '';
  const chosenAvailable = engineers.some((engineer) => engineer.id === chosenEngineerId);
  const [engineerId, setEngineerId] = useState(() => (chosenAvailable ? chosenEngineerId : busiest(initialTime)));
  // Инженера со страницы бригады диспетчер уже выбрал сам, поэтому смена времени его не меняет.
  const [engineerTouched, setEngineerTouched] = useState(chosenAvailable);
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
    if (await applyEvent(unavailableEvent(engineerId, time))) closeEngineerDialog();
  };

  return (
    <div className="dialog dialog--floating" role="dialog" aria-label="Инженер недоступен">
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
                {`${engineer.name} (визитов после ${countTime}: ${visitsFrom(state.plan, engineer.id, countTime)})`}
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
          <button type="button" className="btn btn-ghost" onClick={closeEngineerDialog}>
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
