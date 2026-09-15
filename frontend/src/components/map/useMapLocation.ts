import { useEffect, useRef, useState } from 'react';
import { overviewBounds } from '../../lib/mapView';
import { useAppStore } from '../../store/useAppStore';
import type { LngLat, MapLocation } from './yandexLoader';

const MOSCOW_CENTER: LngLat = [37.6176, 55.7558];
export const REQUEST_ZOOM = 14;
export const LOCATION_ANIMATION_MS = 400;

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
   * Повторно задаёт ту же камеру. Яндекс Карты запоминают размер контейнера при создании
   * и догружают тайлы только после смены камеры, поэтому после того как раскладка
   * устоялась или контейнер изменил размер, камеру нужно «передёрнуть».
   */
  const refreshLocation = () => {
    setLocation((current) => {
      const { duration: _duration, ...rest } = current;
      return { ...rest } as MapLocation;
    });
  };

  return { location, showWholePlan, refreshLocation };
}
