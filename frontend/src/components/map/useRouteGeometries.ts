import { useEffect, useState } from 'react';
import { getRouteGeometry } from '../../api/client';
import type { Plan, PlanningState, RouteLeg } from '../../api/types';
import { byId, straightLegs } from '../../lib/planView';

const cache = new Map<string, RouteLeg[]>();

/** Для тестов: кэш живёт на уровне модуля. */
export function clearRouteGeometryCache(): void {
  cache.clear();
}

/** Геометрия маршрутов по инженерам: сразу прямые отрезки, затем линии дорог с backend. */
export function useRouteGeometries(
  datasetId: string | null,
  state: PlanningState | null,
  plan: Plan | null,
  planKind: 'current' | 'previous',
): Map<string, RouteLeg[]> {
  const [legs, setLegs] = useState<Map<string, RouteLeg[]>>(() => new Map());

  useEffect(() => {
    if (!datasetId || !state || !plan) {
      setLegs(new Map());
      return;
    }
    let alive = true;
    const requests = byId(state.requests);
    const engineers = byId(state.engineers);
    const routes = plan.routes.filter((route) => route.visits.length > 0);
    const keyOf = (engineerId: string, version: number = state.version) => `${datasetId}:${version}:${planKind}:${engineerId}`;

    const initial = new Map<string, RouteLeg[]>();
    for (const route of routes) {
      const engineer = engineers.get(route.engineer_id);
      if (engineer) initial.set(route.engineer_id, cache.get(keyOf(route.engineer_id)) ?? straightLegs(route, engineer, requests));
    }
    setLegs(initial);

    void Promise.all(
      routes.map(async (route) => {
        const key = keyOf(route.engineer_id);
        if (cache.has(key)) return;
        try {
          const geometry = await getRouteGeometry(datasetId, route.engineer_id, planKind);
          // Пока шёл запрос, часы могли перевести план на другое время: линии запоминаются под версию ответа,
          // и линии чужого плана на карту не попадают. Сервер без версии в ответе считается ответившим про этот план.
          cache.set(keyOf(route.engineer_id, geometry.version ?? state.version), geometry.legs);
        } catch {
          // остаются прямые отрезки
        }
      }),
    ).then(() => {
      if (!alive) return;
      setLegs((previous) => {
        const next = new Map(previous);
        for (const route of routes) {
          const cached = cache.get(keyOf(route.engineer_id));
          if (cached) next.set(route.engineer_id, cached);
        }
        return next;
      });
    });

    return () => {
      alive = false;
    };
  }, [datasetId, state, plan, planKind]);

  return legs;
}
