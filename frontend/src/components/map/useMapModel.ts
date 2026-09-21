import { useMemo } from 'react';
import { buildMapModel, NO_CLOCK_LEGS, withClockLegs, type ClockLayer, type MapModel, type MarkerTarget } from '../../lib/mapModel';
import { useAppStore } from '../../store/useAppStore';
import { useClockLayer } from './useClockLayer';
import { useRouteGeometries } from './useRouteGeometries';
import type { LngLat } from './yandexLoader';

/** Что рисует карта: план и поверх него слой часов дня. */
export interface MapLayers {
  /** Маркеры и линии плана; проеханные отрезки уже приглушены, текущий отдан слою часов. */
  model: MapModel;
  /** Где инженеры сейчас; null — плана нет. */
  clock: ClockLayer | null;
}

/** Слои текущего состояния для любой подложки: Яндекс Карт или OpenStreetMap. */
export function useMapLayers(): MapLayers | null {
  const state = useAppStore((s) => s.state);
  const datasetId = useAppStore((s) => s.datasetId);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const plan = state?.plan ?? null;
  const legs = useRouteGeometries(datasetId, state);
  const clock = useClockLayer(state, plan, legs);

  // Часы в эту модель не входят: пока диспетчер двигает их внутри одного перегона, план не пересобирается.
  const base = useMemo(
    () => (state && plan ? buildMapModel({ state, plan, legs, selectedRequestId, selectedEngineerId }) : null),
    [state, plan, legs, selectedRequestId, selectedEngineerId],
  );
  const clockLegs = clock?.legs;
  const model = useMemo(
    () => (base ? { ...base, polylines: withClockLegs(base.polylines, clockLegs ?? NO_CLOCK_LEGS) } : null),
    [base, clockLegs],
  );

  return model ? { model, clock } : null;
}

/** Клик по маркеру заявки закрывает меню карты и выбирает заявку. В режиме выбора точки маркеры ничего не выбирают. */
export function activateMarker(target: MarkerTarget): void {
  if (target.kind !== 'request') return;
  const { pickMode, selectRequest, closeMapMenu } = useAppStore.getState();
  closeMapMenu();
  if (pickMode) return;
  selectRequest(target.requestId);
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
