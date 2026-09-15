import { useState, type FormEvent } from 'react';
import type { Skill, Transport } from '../../api/types';
import { buildUrgentEvent, defaultUrgentWindow, newUrgentId, validateUrgentForm, type UrgentForm } from '../../lib/events';
import { isValidTime, laterTime, SKILL_LABELS, TRANSPORT_LABELS } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

const SKILLS: Skill[] = ['emergency', 'connection', 'local'];
const TRANSPORTS: Transport[] = ['car', 'foot', 'bike', 'public'];

export function UrgentRequestDialog({ onClose }: { onClose: () => void }) {
  const state = useAppStore((s) => s.state);
  const eventTime = useAppStore((s) => s.eventTime);
  const busy = useAppStore((s) => s.busy);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const pickMode = useAppStore((s) => s.pickMode);
  const pickedPoint = useAppStore((s) => s.pickedPoint);
  const startPick = useAppStore((s) => s.startPick);
  const finishPick = useAppStore((s) => s.finishPick);
  const now = state?.now ?? '00:00';
  const engineers = state?.engineers ?? [];
  const initialTime = isValidTime(eventTime) ? laterTime(eventTime, now) : now;
  const [form, setForm] = useState<UrgentForm>(() => ({
    address: '',
    point: null,
    ...defaultUrgentWindow(initialTime, engineers),
    durationMin: 60,
    skill: 'emergency',
    transport: 'car',
    time: initialTime,
  }));
  // Пока диспетчер не правил окно руками, окно следует за временем события.
  const [windowTouched, setWindowTouched] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const point = pickedPoint ?? form.point;

  const update = <K extends keyof UrgentForm>(key: K, value: UrgentForm[K]) => setForm((prev) => ({ ...prev, [key]: value }));

  const updateWindow = (key: 'windowStart' | 'windowEnd', value: string) => {
    setWindowTouched(true);
    update(key, value);
  };

  const updateTime = (value: string) =>
    setForm((prev) => ({
      ...prev,
      time: value,
      ...(windowTouched || !isValidTime(value) ? {} : defaultUrgentWindow(value, engineers)),
    }));

  const close = () => {
    finishPick(null);
    onClose();
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const candidate = { ...form, point };
    const found = validateUrgentForm(candidate, now);
    setErrors(found);
    if (found.length > 0) return;
    const ok = await applyEvent(buildUrgentEvent(candidate, newUrgentId(Date.now())));
    if (ok) close();
  };

  return (
    <div className="dialog" role="dialog" aria-label="Срочная заявка">
      <form noValidate onSubmit={(event) => void submit(event)}>
        <h3>Срочная заявка</h3>
        <label className="field">
          <span>Адрес</span>
          <input value={form.address} onChange={(event) => update('address', event.target.value)} placeholder="Город Москва, ул.Ташкентская, д. 16к2" />
        </label>
        <div className="field-row">
          <button type="button" className="btn btn-small" onClick={startPick} disabled={pickMode}>
            {pickMode ? 'Кликните по карте…' : 'Указать точку на карте'}
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
            <input type="time" value={form.windowStart} onChange={(event) => updateWindow('windowStart', event.target.value)} />
          </label>
          <label className="field">
            <span>Окно до</span>
            <input type="time" value={form.windowEnd} onChange={(event) => updateWindow('windowEnd', event.target.value)} />
          </label>
          <label className="field">
            <span>Длительность, мин</span>
            <input type="number" min={5} step={5} value={form.durationMin} onChange={(event) => update('durationMin', Number(event.target.value))} />
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
        </div>
        <label className="field">
          <span>Время события</span>
          <input type="time" value={form.time} min={now} onChange={(event) => updateTime(event.target.value)} />
        </label>
        {errors.length > 0 && (
          <ul className="error-list" role="alert">
            {errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        )}
        <div className="dialog__actions">
          <button type="button" className="btn btn-ghost" onClick={close}>
            Отмена
          </button>
          <button type="submit" className="btn btn-danger" disabled={busy}>
            Добавить и перепланировать
          </button>
        </div>
      </form>
    </div>
  );
}
