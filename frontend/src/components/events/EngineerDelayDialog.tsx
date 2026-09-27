import { useState, type FormEvent } from 'react';
import type { HHMM, PlanningState } from '../../api/types';
import { beforeShiftsHint, busiestEngineerId, DELAY_PRESETS, delayEvent, timeError, validateDelay, visitsFrom } from '../../lib/events';
import { isValidTime } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

/** Задержка по умолчанию, минут: типичная пробка или затянувшаяся работа на объекте. */
const DEFAULT_DELAY_MIN = 30;

/**
 * Диалог «Задержка инженера»: один на экран, открывается со страницы бригады и из карточки заявки, визит которой
 * «В работе» или бригада «В пути» к нему. Из карточки подставлена бригада визита, время в обоих случаях — часов.
 */
export function EngineerDelayDialog() {
  const state = useAppStore((s) => s.state);
  const open = useAppStore((s) => s.delayDialogOpen);
  const delayEngineerId = useAppStore((s) => s.delayEngineerId);
  if (!state || !open || delayEngineerId === null) return null;
  // Ключ по инженеру: кнопка на странице другой бригады или в карточке заявки другой бригады заполняет форму заново.
  return <DelayForm key={delayEngineerId} state={state} chosenEngineerId={delayEngineerId} />;
}

function DelayForm({ state, chosenEngineerId }: { state: PlanningState; chosenEngineerId: string }) {
  const busy = useAppStore((s) => s.busy);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const closeDelay = useAppStore((s) => s.closeDelay);
  const engineers = state.engineers.filter((engineer) => engineer.available);
  // Время события с часов дня в момент открытия: часы могут идти дальше, время в форме остаётся.
  const [initialTime] = useState<HHMM>(() => useAppStore.getState().clock);
  const [time, setTime] = useState<HHMM>(initialTime);
  // Бригада уже недоступна (план сменился, пока диалог открыт): предлагаем того, у кого больше всего визитов
  // после этого времени, иначе задержка ничего не изменит.
  const busiest = (at: HHMM) => busiestEngineerId(engineers, state.plan, at) ?? '';
  const chosenAvailable = engineers.some((engineer) => engineer.id === chosenEngineerId);
  const [engineerId, setEngineerId] = useState(() => (chosenAvailable ? chosenEngineerId : busiest(initialTime)));
  // Инженера со страницы бригады или из карточки заявки диспетчер уже выбрал сам, поэтому смена времени его не меняет.
  const [engineerTouched, setEngineerTouched] = useState(chosenAvailable);
  const [minutes, setMinutes] = useState(String(DEFAULT_DELAY_MIN));
  const [errors, setErrors] = useState<string[]>([]);
  const countTime = isValidTime(time) ? time : initialTime;
  const hint = beforeShiftsHint(time, state.engineers);

  const changeTime = (value: string) => {
    setTime(value);
    if (!engineerTouched && isValidTime(value)) setEngineerId(busiest(value));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const delayMin = minutes.trim() === '' ? Number.NaN : Number(minutes);
    const found = [engineerId ? null : 'Выберите инженера', validateDelay(delayMin), timeError(time)].filter(
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
          <input type="time" value={time} onChange={(event) => changeTime(event.target.value)} />
        </label>
        {hint && <p className="muted field-note">{hint}</p>}
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
