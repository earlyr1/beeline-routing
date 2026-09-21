const ASAP_HINT =
  'Начало с времени события до конца смен. Ожидание до 4 часов без штрафа, дольше небольшой штраф. Если сегодня никто не успевает, заявка останется неназначенной.';

interface AsapToggleProps {
  checked: boolean;
  onChange(checked: boolean): void;
  /** Поставить фокус на отметку при появлении: так раскрытая форма не теряет место клавиатуры. */
  autoFocus?: boolean;
}

/**
 * Отметка «Как можно скорее» с подсказкой: одна для срочной заявки и для изменения заявки.
 * Окно такой заявки задаёт сервер, поэтому диалог прячет поля окна, пока отметка стоит.
 */
export function AsapToggle({ checked, onChange, autoFocus = false }: AsapToggleProps) {
  return (
    <>
      <label className="field-check">
        <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} autoFocus={autoFocus} />
        <span>Как можно скорее</span>
      </label>
      {checked && <p className="muted field-note">{ASAP_HINT}</p>}
    </>
  );
}
