import { useEffect, useState, type FormEvent } from 'react';
import type { HHMM, Priority, ServiceRequest, Skill, Transport } from '../../api/types';
import {
  requestChanges,
  requestEditForm,
  requestUpdateEvent,
  updatedRequest,
  validateRequestEdit,
  type RequestEditForm,
} from '../../lib/events';
import { isValidTime, laterTime, PRIORITY_LABELS, SKILL_LABELS, TRANSPORT_LABELS } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

const SKILLS = Object.keys(SKILL_LABELS) as Skill[];
const PRIORITIES = Object.keys(PRIORITY_LABELS) as Priority[];
const TRANSPORTS = Object.keys(TRANSPORT_LABELS) as Transport[];

/** Диалог «Изменить заявку»: один на экран, открывается из списка заявок и из карточки объяснения. */
export function RequestEditDialog() {
  const state = useAppStore((s) => s.state);
  const editingRequestId = useAppStore((s) => s.editingRequestId);
  const request = state?.requests.find((item) => item.id === editingRequestId);
  if (!state || !request) return null;
  // Ключ по номеру: при переходе к другой заявке форма заполняется заново.
  return <EditRequestForm key={request.id} original={request} now={state.now} />;
}

function EditRequestForm({ original, now }: { original: ServiceRequest; now: HHMM }) {
  const eventTime = useAppStore((s) => s.eventTime);
  const busy = useAppStore((s) => s.busy);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const pickMode = useAppStore((s) => s.pickMode);
  const pickFor = useAppStore((s) => s.pickFor);
  const pickedPoint = useAppStore((s) => s.pickedPoint);
  const startPick = useAppStore((s) => s.startPick);
  const closeEdit = useAppStore((s) => s.closeEdit);
  const [form, setForm] = useState<RequestEditForm>(() => requestEditForm(original));
  const [time, setTime] = useState(() => (isValidTime(eventTime) ? laterTime(eventTime, now) : now));
  const [errors, setErrors] = useState<string[]>([]);
  // Точку с карты хранит стор, но видит её только этот диалог, если выбор начал он; в форме она остаётся и после.
  const picking = pickMode && pickFor === 'edit';
  const ownPoint = pickFor === 'edit' ? pickedPoint : null;
  useEffect(() => {
    if (ownPoint) setForm((prev) => ({ ...prev, point: ownPoint }));
  }, [ownPoint]);
  const point = ownPoint ?? form.point;
  const candidate: RequestEditForm = { ...form, point };
  const changes = requestChanges(original, updatedRequest(original, candidate));

  const update = <K extends keyof RequestEditForm>(key: K, value: RequestEditForm[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const found = validateRequestEdit(original, candidate, time, now);
    setErrors(found);
    if (found.length > 0) return;
    if (await applyEvent(requestUpdateEvent(original, candidate, time))) closeEdit();
  };

  return (
    <div className="dialog dialog--floating" role="dialog" aria-label="Изменить заявку">
      <form noValidate onSubmit={(event) => void submit(event)}>
        <h3>{`Изменить заявку ${original.id}`}</h3>
        <label className="field">
          <span>Адрес</span>
          <input value={form.address} onChange={(event) => update('address', event.target.value)} />
        </label>
        <div className="field-row">
          <button type="button" className="btn btn-small" onClick={() => startPick('edit')} disabled={picking}>
            {picking ? 'Кликните по карте…' : 'Указать точку на карте'}
          </button>
          {point && (
            <span className="muted">
              Точка: {point.lat.toFixed(5)}, {point.lon.toFixed(5)}
            </span>
          )}
        </div>
        <div className="field-row">
          <label className="field">
            <span>Окно с</span>
            <input type="time" value={form.windowStart} onChange={(event) => update('windowStart', event.target.value)} />
          </label>
          <label className="field">
            <span>Окно до</span>
            <input type="time" value={form.windowEnd} onChange={(event) => update('windowEnd', event.target.value)} />
          </label>
          <label className="field">
            <span>Длительность, мин</span>
            <input
              type="number"
              min={5}
              step={5}
              value={form.durationMin}
              onChange={(event) => update('durationMin', Number(event.target.value))}
            />
          </label>
        </div>
        <div className="field-row">
          <label className="field">
            <span>Навык</span>
            <select value={form.skill} onChange={(event) => update('skill', event.target.value as Skill)}>
              {SKILLS.map((skill) => (
                <option key={skill} value={skill}>
                  {SKILL_LABELS[skill]}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Приоритет</span>
            <select value={form.priority} onChange={(event) => update('priority', event.target.value as Priority)}>
              {PRIORITIES.map((priority) => (
                <option key={priority} value={priority}>
                  {PRIORITY_LABELS[priority]}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="field-row">
          <label className="field">
            <span>Транспорт</span>
            <select value={form.transport} onChange={(event) => update('transport', event.target.value as Transport | '')}>
              <option value="">Не требуется</option>
              {TRANSPORTS.map((transport) => (
                <option key={transport} value={transport}>
                  {TRANSPORT_LABELS[transport]}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Время события</span>
            <input type="time" value={time} min={now} onChange={(event) => setTime(event.target.value)} />
          </label>
        </div>
        {changes.length > 0 && <p className="muted">{`Изменится: ${changes.join(', ')}`}</p>}
        {errors.length > 0 && (
          <ul className="error-list" role="alert">
            {errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        )}
        <div className="dialog__actions">
          <button type="button" className="btn btn-ghost" onClick={closeEdit}>
            Отмена
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Сохранить и перепланировать
          </button>
        </div>
      </form>
    </div>
  );
}
