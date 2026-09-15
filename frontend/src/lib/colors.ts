// Красный зарезервирован под срочные заявки, серый под неназначенные.
export const ENGINEER_PALETTE = [
  '#2563eb',
  '#16a34a',
  '#ea580c',
  '#9333ea',
  '#0891b2',
  '#ca8a04',
  '#db2777',
  '#4f46e5',
  '#65a30d',
  '#0d9488',
  '#7c3aed',
  '#b45309',
  '#0284c7',
  '#be185d',
];
export const UNASSIGNED_COLOR = '#9ca3af';

export function engineerColor(engineerId: string | null | undefined, engineerIds: string[]): string {
  if (!engineerId) return UNASSIGNED_COLOR;
  const index = engineerIds.indexOf(engineerId);
  return index < 0 ? UNASSIGNED_COLOR : ENGINEER_PALETTE[index % ENGINEER_PALETTE.length];
}
