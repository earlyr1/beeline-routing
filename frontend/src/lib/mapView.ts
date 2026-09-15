import type { PlanningState } from '../api/types';
import type { LngLat } from '../components/map/yandexLoader';

/**
 * Прямоугольник, в который помещаются офис, старты инженеров и все заявки с координатами.
 * Формат LngLatBounds из JS API v3: [верхний левый, нижний правый].
 */
export function overviewBounds(state: PlanningState, padding = 0.08): [LngLat, LngLat] {
  const points: LngLat[] = [
    [state.office.lon, state.office.lat],
    ...state.engineers.map((engineer): LngLat => [engineer.start_lon, engineer.start_lat]),
    ...state.requests.flatMap((request): LngLat[] =>
      request.lat === null || request.lon === null ? [] : [[request.lon, request.lat]],
    ),
  ];
  const lons = points.map((point) => point[0]);
  const lats = points.map((point) => point[1]);
  const [minLon, maxLon, minLat, maxLat] = [Math.min(...lons), Math.max(...lons), Math.min(...lats), Math.max(...lats)];
  const padLon = Math.max((maxLon - minLon) * padding, 0.01);
  const padLat = Math.max((maxLat - minLat) * padding, 0.01);
  return [
    [minLon - padLon, maxLat + padLat],
    [maxLon + padLon, minLat - padLat],
  ];
}
