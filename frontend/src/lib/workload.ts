/**
 * Нагрузка инженеров: зеркало таблицы уровней backend.
 * Уровень задаёт, во сколько оптимизатору обходится новый инженер, и запас времени на дорогу.
 */
export interface WorkloadLevel {
  level: number;
  title: string;
  emoji: string;
  hint: string;
  /** Во сколько раз планируемое время в пути больше расчётного. */
  travelFactor: number;
}

const MORE_ENGINEERS = 'Больше инженеров, у каждого свободнее день';
const BALANCE = 'Баланс между числом инженеров и пробегом';
const FEWER_ENGINEERS = 'Меньше инженеров, каждому больше заявок';

export const WORKLOAD_LEVELS: readonly WorkloadLevel[] = [
  { level: 0, title: 'Спокойный день', emoji: '😌', hint: MORE_ENGINEERS, travelFactor: 1.3 },
  { level: 1, title: 'Без спешки', emoji: '🙂', hint: MORE_ENGINEERS, travelFactor: 1.2 },
  { level: 2, title: 'Обычный день', emoji: '😐', hint: BALANCE, travelFactor: 1.1 },
  { level: 3, title: 'Плотный день', emoji: '😓', hint: FEWER_ENGINEERS, travelFactor: 1.05 },
  { level: 4, title: 'На пределе', emoji: '🥵', hint: FEWER_ENGINEERS, travelFactor: 1 },
];

export const MIN_WORKLOAD_LEVEL = 0;
export const MAX_WORKLOAD_LEVEL = WORKLOAD_LEVELS.length - 1;
export const DEFAULT_WORKLOAD_LEVEL = 2;

/** Целый уровень внутри шкалы; нечисловое значение превращается в обычный день. */
export function clampWorkloadLevel(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_WORKLOAD_LEVEL;
  return Math.min(MAX_WORKLOAD_LEVEL, Math.max(MIN_WORKLOAD_LEVEL, Math.round(value)));
}

export const workloadLevel = (value: number): WorkloadLevel => WORKLOAD_LEVELS[clampWorkloadLevel(value)];

/** «Запас на дорогу: +30%» по коэффициенту времени в пути; у дня на пределе запаса нет. */
export function travelBufferText(level: number): string {
  const percent = Math.round((workloadLevel(level).travelFactor - 1) * 100);
  return `Запас на дорогу: ${percent > 0 ? `+${percent}%` : 'без запаса'}`;
}
