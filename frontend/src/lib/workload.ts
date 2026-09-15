/**
 * Нагрузка инженеров и обед по плану: зеркало таблицы уровней backend.
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

export const WORKLOAD_LEVELS: readonly WorkloadLevel[] = [
  { level: 0, title: 'Спокойный день', emoji: '😌', hint: 'Больше инженеров, больше запас на дорогу', travelFactor: 1.3 },
  { level: 1, title: 'Обычный день', emoji: '😐', hint: 'Баланс между числом инженеров и пробегом', travelFactor: 1.1 },
  { level: 2, title: 'На пределе', emoji: '🥵', hint: 'Меньше инженеров, каждому больше заявок', travelFactor: 1 },
];

export const MIN_WORKLOAD_LEVEL = 0;
export const MAX_WORKLOAD_LEVEL = WORKLOAD_LEVELS.length - 1;
export const DEFAULT_WORKLOAD_LEVEL = 1;

/** Обед по плану включён по умолчанию, как и в сессии на сервере. */
export const DEFAULT_LUNCH_ENABLED = true;

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

/** Обед сессии; состояние от прежнего backend без флага обеда считается днём с обедом. */
export const lunchEnabledOf = (value: boolean | undefined): boolean => value ?? DEFAULT_LUNCH_ENABLED;

/** «😐 Обычный день · с обедом»: нагрузка и обед дня одной строкой. */
export function dayModeText(level: number, lunch: boolean): string {
  const { emoji, title } = workloadLevel(level);
  return `${emoji} ${title} · ${lunch ? 'с обедом' : 'без обеда'}`;
}
