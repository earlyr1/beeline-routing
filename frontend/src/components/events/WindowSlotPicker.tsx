import type { HHMM, TimeSlot } from '../../api/types';
import { formatWindow } from '../../lib/format';
import { openSlots, slotByKey, slotKey, slotOf, slotOption } from '../../lib/windows';

/**
 * Выбор окна визита: диспетчер предлагает клиенту слот сетки, а не произвольный интервал.
 *
 * Слоты приходят от сервера; своих у диалога нет. Слот, который к времени события уже закончился, в список не
 * попадает: такое окно сервер всё равно отклоняет, — а идущий слот подписан, чтобы было видно, где мы во времени
 * дня. Окно заявки, которого в сетке нет (заявка из данных, у аварии там весь день), показывается первой строкой
 * как есть: молча подменять окно клиента диалог не вправе, а вот любой новый выбор уже идёт по сетке.
 * Окно «как можно скорее» задаёт сервер, и клиенту его не называли: вместо него строка «Выберите окно» (unnamed).
 */
export function WindowSlotPicker({
  grid,
  start,
  end,
  time,
  unnamed = false,
  onChange,
}: {
  grid: TimeSlot[];
  start: HHMM;
  end: HHMM;
  /** Время события: слоты до него уже прошли. */
  time: HHMM;
  /** Окно в форме клиенту не называли (его задал сервер заявке «как можно скорее»): слот выбирают заново. */
  unnamed?: boolean;
  onChange: (slot: TimeSlot) => void;
}) {
  const current = slotOf(grid, start, end);
  const open = openSlots(grid, time);
  // Слот самой заявки остаётся в списке, даже если он прошёл: иначе выбранного значения в списке просто нет.
  const slots = current && !open.includes(current) ? [current, ...open] : open;
  const placeholder = slots.length === 0 ? 'Слоты сетки на сегодня закончились' : unnamed ? 'Выберите окно' : formatWindow(start, end);
  return (
    <label className="field">
      <span>Окно визита</span>
      <select
        value={current ? slotKey(current) : ''}
        onChange={(event) => {
          const slot = slotByKey(grid, event.target.value);
          if (slot) onChange(slot);
        }}
      >
        {!current && <option value="">{placeholder}</option>}
        {slots.map((slot) => (
          <option key={slotKey(slot)} value={slotKey(slot)}>
            {slotOption(slot, time)}
          </option>
        ))}
      </select>
    </label>
  );
}
