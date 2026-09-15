import { useState, type FormEvent } from 'react';
import type { PlanningState, Transport } from '../../api/types';
import {
  busiestEngineerId,
  carDowngradeHint,
  carRequiredVisitsFrom,
  defaultNewTransport,
  effectiveEventTime,
  timeError,
  transportChangeEvent,
  visitsFrom,
} from '../../lib/events';
import { isValidTime, TRANSPORT_LABELS } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

const TRANSPORTS = Object.keys(TRANSPORT_LABELS) as Transport[];

/** Диалог «Смена транспорта»: один на экран, открывается со страницы бригады для её инженера. */
export function TransportChangeDialog() {
  const state = useAppStore((s) => s.state);
  const dialog = useAppStore((s) => s.engineerDialog);
  if (!state || dialog?.kind !== 'transport') return null;
  // Ключ по инженеру: кнопка на странице другой бригады заполняет форму заново.
  return <TransportForm key={dialog.engineerId} state={state} chosenEngineerId={dialog.engineerId} />;
}

function TransportForm({ state, chosenEngineerId }: { state: PlanningState; chosenEngineerId: string }) {
  const eventTime = useAppStore((s) => s.eventTime);
  const busy = useAppStore((s) => s.busy);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const closeEngineerDialog = useAppStore((s) => s.closeEngineerDialog);
  const { now } = state;
  const engineers = state.engineers.filter((engineer) => engineer.available);
  const initialTime = effectiveEventTime(eventTime, now);
  const [time, setTime] = useState(initialTime);
  // Инженер бригады мог стать недоступным: тогда предлагаем того, у кого больше всего визитов после этого времени.
  const busiest = (at: string) => busiestEngineerId(engineers, state.plan, at) ?? '';
  const chosenAvailable = engineers.some((engineer) => engineer.id === chosenEngineerId);
  const [engineerId, setEngineerId] = useState(() => (chosenAvailable ? chosenEngineerId : busiest(initialTime)));
  // Инженера со страницы бригады диспетчер уже выбрал сам, поэтому смена времени его не меняет.
  const [engineerTouched, setEngineerTouched] = useState(chosenAvailable);
  // Пока диспетчер сам не выбрал транспорт, предлагаем вариант по умолчанию для текущего инженера.
  const [pickedTransport, setPickedTransport] = useState<Transport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const countTime = isValidTime(time) ? time : now;

  const current = engineers.find((engineer) => engineer.id === engineerId)?.transport ?? null;
  const options = TRANSPORTS.filter((transport) => transport !== current);
  const fallback = current ? defaultNewTransport(current) : 'car';
  const transport = pickedTransport !== null && pickedTransport !== current ? pickedTransport : fallback;

  let hint: string | null = null;
  if (current === 'car') {
    const carOnly = carRequiredVisitsFrom(state.plan, state.requests, engineerId, countTime);
    if (carOnly > 0) hint = carDowngradeHint(carOnly, countTime);
  } else if (transport === 'car') {
    hint = 'Инженер сможет брать заявки, которым нужен автомобиль';
  }

  const changeTime = (value: string) => {
    setTime(value);
    if (!engineerTouched && isValidTime(value)) setEngineerId(busiest(value));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const problem = engineerId ? timeError(time, now) : 'Выберите инженера';
    setError(problem);
    if (problem) return;
    if (await applyEvent(transportChangeEvent(engineerId, transport, time))) closeEngineerDialog();
  };

  return (
    <div className="dialog dialog--floating" role="dialog" aria-label="Смена транспорта">
      <form noValidate onSubmit={(event) => void submit(event)}>
        <h3>Смена транспорта</h3>
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
                {`${engineer.name} · ${TRANSPORT_LABELS[engineer.transport]} (визитов после ${countTime}: ${visitsFrom(
                  state.plan,
                  engineer.id,
                  countTime,
                )})`}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Новый транспорт</span>
          <select value={transport} onChange={(event) => setPickedTransport(event.target.value as Transport)}>
            {options.map((item) => (
              <option key={item} value={item}>
                {TRANSPORT_LABELS[item]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Сменить с</span>
          <input type="time" value={time} min={now} onChange={(event) => changeTime(event.target.value)} />
        </label>
        <p className="muted">
          Начатые до этого времени визиты и текущий переезд останутся как запланированы, дальше маршрут считается на новом
          транспорте.
        </p>
        {hint && <p className="muted">{hint}</p>}
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
