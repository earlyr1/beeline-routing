import { useMemo } from 'react';
import { buildMapModel, type MapModel, type MarkerTarget } from '../../lib/mapModel';
import { displayedPlan } from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';
import { useRouteGeometries } from './useRouteGeometries';
import type { LngLat } from './yandexLoader';

/** Маркеры и линии текущего состояния для любой подложки: Яндекс Карт или OpenStreetMap. */
export function useMapModel(): MapModel | null {
  const state = useAppStore((s) => s.state);
  const datasetId = useAppStore((s) => s.datasetId);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const plan = state ? displayedPlan(state, showPrevious) : null;
  const legs = useRouteGeometries(datasetId, state, plan, showPrevious ? 'previous' : 'current');

  return useMemo(
    () => (state && plan ? buildMapModel({ state, plan, legs, showPrevious, selectedRequestId, selectedEngineerId }) : null),
    [state, plan, legs, showPrevious, selectedRequestId, selectedEngineerId],
  );
}

/** Клик по маркеру закрывает меню карты. В режиме выбора точки маркеры ничего не выбирают. */
export function activateMarker(target: MarkerTarget): void {
  const { pickMode, selectedEngineerId, selectRequest, selectEngineer, closeMapMenu } = useAppStore.getState();
  closeMapMenu();
  if (pickMode) return;
  if (target.kind === 'request') selectRequest(target.requestId);
  if (target.kind === 'engineer') selectEngineer(selectedEngineerId === target.engineerId ? null : target.engineerId);
}

/**
 * Клик по карте: в режиме выбора точки передаёт координаты диалогу, который начал выбор, иначе открывает меню карты.
 * Меню открывается только по пустому месту: onEmptyPlace false у клика по маркеру или другому объекту карты.
 */
export function pickPoint([lon, lat]: LngLat, onEmptyPlace = true): void {
  const { pickMode, finishPick, openMapMenu } = useAppStore.getState();
  if (pickMode) finishPick({ lon, lat });
  else if (onEmptyPlace) openMapMenu({ lat, lon });
}
