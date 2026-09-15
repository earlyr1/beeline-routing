import { useEffect, useRef, useState } from 'react';
import { overviewBounds } from '../../lib/mapView';
import { useAppStore } from '../../store/useAppStore';
import type { LngLat, MapLocation } from './yandexLoader';

const MOSCOW_CENTER: LngLat = [37.6176, 55.7558];
export const REQUEST_ZOOM = 14;
export const LOCATION_ANIMATION_MS = 400;
/** Незаметный сдвиг камеры (около метра), чтобы Яндекс Карты пересчитали покрытие тайлами. */
export const NUDGE_DEGREES = 1e-5;
export const NUDGE_ANIMATION_MS = 200;

/**
 * Куда смотрит карта: сначала весь план, при выборе заявки приближение к ней,
 * после снятия выбора снова весь план.
 */
export function useMapLocation(): { location: MapLocation; showWholePlan: () => void; refreshLocation: () => void } {
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const [location, setLocation] = useState<MapLocation>(() => {
    const state = useAppStore.getState().state;
    return state ? { bounds: overviewBounds(state) } : { center: MOSCOW_CENTER, zoom: 10 };
  });
  const firstSelection = useRef(true);
  const nudgeSign = useRef(1);

  const showOverview = () => {
    const current = useAppStore.getState().state;
    if (current) setLocation({ bounds: overviewBounds(current), duration: LOCATION_ANIMATION_MS });
  };

  useEffect(() => {
    if (firstSelection.current) {
      firstSelection.current = false;
      if (!selectedRequestId) return;
    }
    if (!selectedRequestId) {
      showOverview();
      return;
    }
    const request = useAppStore.getState().state?.requests.find((item) => item.id === selectedRequestId);
    if (request && request.lat !== null && request.lon !== null) {
      setLocation({ center: [request.lon, request.lat], zoom: REQUEST_ZOOM, duration: LOCATION_ANIMATION_MS });
    }
  }, [selectedRequestId]);

  const showWholePlan = () => {
    if (useAppStore.getState().selectedRequestId) useAppStore.getState().selectRequest(null);
    else showOverview();
  };

  /**
   * «Подталкивает» камеру на незаметный сдвиг с короткой анимацией. Яндекс Карты при первом
   * показе покрывают тайлами только часть области и пересчитывают покрытие лишь при реальном
   * движении камеры; установка той же камеры ничего не меняет. Знак сдвига чередуется,
   * поэтому камера не уползает.
   */
  const refreshLocation = () => {
    setLocation((current) => {
      nudgeSign.current = -nudgeSign.current;
      const shift = NUDGE_DEGREES * nudgeSign.current;
      if ('bounds' in current) {
        const [[left, top], [right, bottom]] = current.bounds;
        return { bounds: [[left + shift, top], [right + shift, bottom]], duration: NUDGE_ANIMATION_MS };
      }
      return { center: [current.center[0] + shift, current.center[1]], zoom: current.zoom, duration: NUDGE_ANIMATION_MS };
    });
  };

  return { location, showWholePlan, refreshLocation };
}
