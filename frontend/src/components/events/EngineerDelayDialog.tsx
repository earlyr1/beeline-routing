import { useState, type FormEvent } from 'react';
import type { HHMM, PlanningState } from '../../api/types';
import { busiestEngineerId, DELAY_PRESETS, delayEvent, timeError, validateDelay, visitsFrom } from '../../lib/events';
import { isValidTime, laterTime } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

/** Задержка по умолчанию, минут: типичная пробка или затянувшаяся работа на объекте. */
const DEFAULT_DELAY_MIN = 30;

/** Диалог «Задержка инженера»: один на экран, открывается из панели событий и из карточки маршрута. */
export function EngineerDelayDialog() {
  const state = useAppStore((s) => s.state);
  const delayEngineerId = useAppStore((s) => s.delayEngineerId);
  if (!state) return null;
  // Ключ по инженеру: кнопка в карточке другого маршрута заполняет форму заново.
  return <DelayForm key={delayEngineerId ?? ''} state={state} chosenEngineerId={delayEngineerId} />;
}

function DelayForm({ state, chosenEngineerId }: { state: PlanningState; chosenEngineerId: string | null }) {
  const eventTime = useAppStore((s) => s.eventTime);
  const busy = useAppStore((s) => s.busy);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const closeDelay = useAppStore((s) => s.closeDelay);
  const { now } = state;
  const engineers = state.engineers.filter((engineer) => engineer.available);
  const initialTime = isValidTime(eventTime) ? laterTime(eventTime, now) : now;
  const [time, setTime] = useState<HHMM>(initialTime);
  // Без выбранного инженера предлагаем того, у кого больше всего визитов после этого времени: иначе задержка ничего не изменит.
  const busiest = (at: HHMM) => busiestEngineerId(engineers, state.plan, at) ?? '';
  const [engineerId, setEngineerId] = useState(() =>
    engineers.some((engineer) => engineer.id === chosenEngineerId) && chosenEngineerId ? chosenEngineerId : busiest(initialTime),
  );
  // Инженера из карточки маршрута диспетчер уже выбрал сам, поэтому смена времени его не меняет.
  const [engineerTouched, setEngineerTouched] = useState(() => engineers.some((engineer) => engineer.id === chosenEngineerId));
  const [minutes, setMinutes] = useState(String(DEFAULT_DELAY_MIN));
  const [errors, setErrors] = useState<string[]>([]);
  const countTime = isValidTime(time) ? time : now;

  const changeTime = (value: string) => {
    setTime(value);
    if (!engineerTouched && isValidTime(value)) setEngineerId(busiest(value));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const delayMin = minutes.trim() === '' ? Number.NaN : Number(minutes);
    const found = [engineerId ? null : 'Выберите инженера', validateDelay(delayMin), timeError(time, now)].filter(
      (problem): problem is string => problem !== null,
    );
    setErrors(found);
    if (found.length > 0) return;
    if (await applyEvent(delayEvent(engineerId, delayMin, time))) closeDelay();
  };

  return (
    <div className="dialog dialog--floating" role="dialog" aria-label="Задержка инженера">
      <form noValidate onSubmit={(event) => void submit(event)}>
        <h3>Задержка инженера</h3>
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
        <div className="field-row">
          <label className="field">
            <span>На сколько минут</span>
            <input type="number" min={5} max={480} step={5} value={minutes} onChange={(event) => setMinutes(event.target.value)} />
          </label>
          <div className="delay-presets" role="group" aria-label="Быстрый выбор задержки">
            {DELAY_PRESETS.map((preset) => (
              <button
                key={preset}
                type="button"
                className="btn btn-small"
                aria-pressed={minutes === String(preset)}
                onClick={() => setMinutes(String(preset))}
              >
                {`${preset} мин`}
              </button>
            ))}
          </div>
        </div>
        <label className="field">
          <span>Задержка с</span>
          <input type="time" value={time} min={now} onChange={(event) => changeTime(event.target.value)} />
        </label>
        <p className="muted">Если инженер не успевает к клиентам, их заявки перейдут другим</p>
        {errors.length > 0 && (
          <ul className="error-list" role="alert">
            {errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        )}
        <div className="dialog__actions">
          <button type="button" className="btn btn-ghost" onClick={closeDelay}>
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
