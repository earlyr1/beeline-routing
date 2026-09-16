import { EQUIPMENT_TITLE } from '../EquipmentBadge';

interface EquipmentToggleProps {
  checked: boolean;
  onChange(checked: boolean): void;
}

/**
 * Отметка «Нужно оборудование»: одна для срочной заявки и для изменения заявки.
 * На расчёт плана она не влияет: по ней бригада знает, что взять с собой утром.
 */
export function EquipmentToggle({ checked, onChange }: EquipmentToggleProps) {
  return (
    <label className="field-check" title={EQUIPMENT_TITLE}>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>Нужно оборудование</span>
    </label>
  );
}
