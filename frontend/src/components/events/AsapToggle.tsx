const ASAP_HINT =
  'Начало с времени события до конца смен. Ожидание до 4 часов без штрафа, дольше небольшой штраф. Если сегодня никто не успевает, заявка останется неназначенной.';

interface AsapToggleProps {
  checked: boolean;
  onChange(checked: boolean): void;
}

/**
 * Отметка «Как можно скорее» с подсказкой: одна для срочной заявки и для изменения заявки.
 * Окно такой заявки задаёт сервер, поэтому диалог прячет поля окна, пока отметка стоит.
 */
export function AsapToggle({ checked, onChange }: AsapToggleProps) {
  return (
    <>
      <label className="field-check">
        <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
        <span>Как можно скорее</span>
      </label>
      {checked && <p className="muted field-note">{ASAP_HINT}</p>}
    </>
  );
}
