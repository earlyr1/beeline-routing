import { EQUIPMENT_TITLE } from '../EquipmentBadge';

interface EquipmentToggleProps {
  checked: boolean;
  onChange(checked: boolean): void;
}

/**
 * Отметка «Нужно оборудование»: одна для срочной заявки и для изменения заявки.
 * На расчёт плана она влияет: заявка тратит единицу дневного запаса, и бригаде, у которой запас
 * разобран, её не отдадут ни солвер, ни «Ничего не менять», вставляя заявку в маршрут выбранной бригады.
 */
export function EquipmentToggle({ checked, onChange }: EquipmentToggleProps) {
  return (
    <label className="field-check" title={EQUIPMENT_TITLE}>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>Нужно оборудование</span>
    </label>
  );
}
