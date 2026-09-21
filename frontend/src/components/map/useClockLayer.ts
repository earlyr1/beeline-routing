import { useMemo } from 'react';
import type { Plan, PlanningState, RouteLeg } from '../../api/types';
import { buildClockLayer, type ClockLayer, type ClockLegs } from '../../lib/mapModel';
import { useAppStore } from '../../store/useAppStore';

/**
 * Слой часов дня: где каждый инженер сейчас и какой отрезок пути он проезжает.
 * Маркеры двигаются десять раз в секунду, поэтому список проеханных отрезков возвращается отдельным объектом,
 * который меняется, только когда инженер доехал до клиента: пока он тот же, линии и маркеры плана не перерисовываются.
 */
export function useClockLayer(state: PlanningState | null, plan: Plan | null, legs: Map<string, RouteLeg[]>): ClockLayer | null {
  const clock = useAppStore((s) => s.clock);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const layer = useMemo(
    () => (state && plan ? buildClockLayer({ state, plan, legs, clock, selectedRequestId, selectedEngineerId }) : null),
    [state, plan, legs, clock, selectedRequestId, selectedEngineerId],
  );
  const passed = layer ? [...layer.legs.passed].join(' ') : '';
  const split = layer ? [...layer.legs.split].join(' ') : '';
  const stable: ClockLegs = useMemo(
    () => ({ passed: new Set(passed ? passed.split(' ') : []), split: new Set(split ? split.split(' ') : []) }),
    [passed, split],
  );
  return layer ? { ...layer, legs: stable } : null;
}
