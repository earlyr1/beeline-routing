import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { Skill, Transport } from '../../api/types';
import {
  beforeShiftsHint,
  buildUrgentEvent,
  defaultUrgentWindow,
  MANUAL_URGENT_FIELDS,
  newUrgentId,
  validateUrgentForm,
  workTypeFields,
  workTypeSummary,
  type PickedPoint,
  type UrgentForm,
} from '../../lib/events';
import { isValidTime, SKILL_LABELS, TRANSPORT_LABELS } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';
import { AsapToggle } from './AsapToggle';
import { EquipmentToggle } from './EquipmentToggle';

const SKILLS: Skill[] = ['emergency', 'connection', 'local'];
const TRANSPORTS: Transport[] = ['car', 'bike', 'public'];

const samePoint = (a: PickedPoint | null, b: PickedPoint | null) => a?.lat === b?.lat && a?.lon === b?.lon;

// Почему у аварии 80 минут, а не 100 из ответов организаторов: дорогу из норматива сервис не берёт, он считает её сам.
const NORM_DURATION_HINT =
  'Норматив — время работ на адресе. В нормативе организаторов есть ещё 20 минут дороги (у аварии 20 + 80 = 100 минут), а дорогу сервис считает сам по карте от прежнего адреса бригады.';

export function UrgentRequestDialog({ onClose }: { onClose: () => void }) {
  const state = useAppStore((s) => s.state);
  const busy = useAppStore((s) => s.busy);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const pickMode = useAppStore((s) => s.pickMode);
  const pickFor = useAppStore((s) => s.pickFor);
  const pickedPoint = useAppStore((s) => s.pickedPoint);
  const startPick = useAppStore((s) => s.startPick);
  const clearPick = useAppStore((s) => s.clearPick);
  const addressLookup = useAppStore((s) => s.urgentAddressLookup);
  const suggestedAddress = useAppStore((s) => s.urgentSuggestedAddress);
  const engineers = state?.engineers ?? [];
  // Типы работ с нормативами организаторов присылает сервер; без них диалог прежний, с выбором навыка.
  const workTypes = useAppStore((s) => s.config?.work_types) ?? [];
  const [form, setForm] = useState<UrgentForm>(() => {
    // Время события с часов дня в момент открытия: часы могут идти дальше, время в форме остаётся.
    const time = useAppStore.getState().clock;
    // Первый тип от сервера — авария: срочным случаем организаторы считают именно её, и диспетчеру хватает места и времени.
    const workType = useAppStore.getState().config?.work_types?.[0];
    return {
      address: '',
      point: null,
      ...defaultUrgentWindow(time, engineers),
      time,
      ...(workType ? workTypeFields(workType) : MANUAL_URGENT_FIELDS),
    };
  });
  // Диспетчер раскрыл значения типа работ, чтобы поправить их; новый тип работ сворачивает их снова.
  const [expanded, setExpanded] = useState(false);
  // Заявке «как можно скорее» по типу работ окно не нужно: форма свёрнута до места и времени события.
  const collapsed = form.workType?.asap === true && !expanded;
  // Пока диспетчер не правил окно руками, окно следует за временем события.
  const [windowTouched, setWindowTouched] = useState(false);
  // Адрес, который диспетчер ввёл сам, адрес найденной по точке не заменяет.
  const [addressTyped, setAddressTyped] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  // Точку из стора видит только диалог, начавший выбор; в форме она остаётся, даже когда карту займёт изменение заявки.
  const picking = pickMode && pickFor === 'urgent';
  const ownPoint = pickFor === 'urgent' ? pickedPoint : null;
  useEffect(() => {
    if (ownPoint) setForm((prev) => ({ ...prev, point: ownPoint }));
  }, [ownPoint]);
  const point = ownPoint ?? form.point;
  const hint = beforeShiftsHint(form.time, engineers);

  // Адрес, который диалог сам подставил по точке из меню карты; null — такого адреса в поле нет.
  const autoAddress = useRef<string | null>(null);
  // Точка из меню карты: пока ищем её адрес, прежний найденный адрес к ней не относится; ничего не нашли — поле пустое.
  useEffect(() => {
    if (addressTyped || addressLookup === 'idle') return;
    const address = addressLookup === 'done' ? (suggestedAddress ?? '') : '';
    autoAddress.current = address || null;
    setForm((prev) => ({ ...prev, address }));
  }, [addressLookup, suggestedAddress, addressTyped]);

  // Диспетчер указал на карте другую точку: подставленный адрес прежней точки к ней не относится и стирается.
  const shownPoint = useRef(point);
  useEffect(() => {
    if (samePoint(shownPoint.current, point)) return;
    shownPoint.current = point;
    const stale = autoAddress.current;
    autoAddress.current = null;
    if (stale === null || addressTyped) return;
    setForm((prev) => (prev.address === stale ? { ...prev, address: '' } : prev));
  }, [point, addressTyped]);

  const update = <K extends keyof UrgentForm>(key: K, value: UrgentForm[K]) => setForm((prev) => ({ ...prev, [key]: value }));

  const updateAddress = (value: string) => {
    setAddressTyped(true);
    update('address', value);
  };

  const updateWindow = (key: 'windowStart' | 'windowEnd', value: string) => {
    setWindowTouched(true);
    update(key, value);
  };

  const chooseWorkType = (typeBk: string) => {
    const workType = workTypes.find((item) => item.source_type_bk === typeBk);
    if (!workType) return;
    setExpanded(false);
    setForm((prev) => ({ ...prev, ...workTypeFields(workType) }));
  };

  const updateTime = (value: string) =>
    setForm((prev) => ({
      ...prev,
      time: value,
      ...(windowTouched || !isValidTime(value) ? {} : defaultUrgentWindow(value, engineers)),
    }));

  const close = () => {
    clearPick('urgent');
    onClose();
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const candidate = { ...form, point };
    const found = validateUrgentForm(candidate);
    setErrors(found);
    if (found.length > 0) return;
    const ok = await applyEvent(buildUrgentEvent(candidate, newUrgentId(Date.now()), engineers));
    if (ok) close();
  };

  return (
    <div className="dialog" role="dialog" aria-label="Срочная заявка">
      <form noValidate onSubmit={(event) => void submit(event)}>
        <h3>Срочная заявка</h3>
        {form.workType && (
          <label className="field">
            <span>Тип работ</span>
            <select value={form.workType.source_type_bk} onChange={(event) => chooseWorkType(event.target.value)}>
              {workTypes.map((workType) => (
                <option key={workType.source_type_bk} value={workType.source_type_bk}>
                  {workType.title}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span>Адрес</span>
          <input value={form.address} onChange={(event) => updateAddress(event.target.value)} placeholder="Город Москва, ул.Ташкентская, д. 16к2" />
        </label>
        {addressLookup === 'loading' && <p className="muted field-note">Ищем адрес…</p>}
        <div className="field-row">
          <button type="button" className="btn btn-small" onClick={() => startPick('urgent')} disabled={picking}>
            {picking ? 'Кликните по карте…' : 'Указать точку на карте'}
          </button>
          {point && (
            <span className="muted">
              Точка: {point.lat.toFixed(5)}, {point.lon.toFixed(5)}
            </span>
          )}
        </div>
        {collapsed ? (
          <p className="work-type-summary">
            <span title={NORM_DURATION_HINT}>{workTypeSummary(form)}</span>
            <button type="button" className="link-button" onClick={() => setExpanded(true)}>
              Изменить
            </button>
          </p>
        ) : (
          <>
            {/* Кнопка «Изменить» пропала вместе со строкой: фокус переходит на первое раскрытое поле. */}
            <AsapToggle checked={form.asap} onChange={(checked) => update('asap', checked)} autoFocus={expanded} />
            <div className="field-row">
              {/* Поля окна скрыты, пока стоит «Как можно скорее»; введённые значения остаются в форме. */}
              {!form.asap && (
                <>
                  <label className="field">
                    <span>Окно с</span>
                    <input type="time" value={form.windowStart} onChange={(event) => updateWindow('windowStart', event.target.value)} />
                  </label>
                  <label className="field">
                    <span>Окно до</span>
                    <input type="time" value={form.windowEnd} onChange={(event) => updateWindow('windowEnd', event.target.value)} />
                  </label>
                </>
              )}
              <label className="field" title={form.workType ? NORM_DURATION_HINT : undefined}>
                <span>Длительность, мин</span>
                <input type="number" min={5} step={5} value={form.durationMin} onChange={(event) => update('durationMin', Number(event.target.value))} />
              </label>
            </div>
            <div className="field-row">
              {/* Навык идёт за типом работ; вручную его выбирают, только когда сервер типов не прислал. */}
              {!form.workType && (
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
              )}
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
            <EquipmentToggle checked={form.needsEquipment} onChange={(checked) => update('needsEquipment', checked)} />
          </>
        )}
        <label className="field">
          <span>Время события</span>
          <input type="time" value={form.time} onChange={(event) => updateTime(event.target.value)} />
        </label>
        {hint && <p className="muted field-note">{hint}</p>}
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
