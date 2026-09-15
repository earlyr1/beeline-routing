import type { Plan, PlanningState, RouteLeg } from '../api/types';
import type { LngLat } from '../components/map/yandexLoader';
import { engineerColor } from './colors';
import { shortAddress } from './format';
import { assignmentIndex, diffMarks, engineerIdsOf, type DiffMark } from './planView';

/** Прозрачность маршрутов других инженеров, когда выбран один: то же, что суффикс `40` у цвета. */
export const DIMMED_ROUTE_OPACITY = 0x40 / 0xff;

export type MarkerTarget =
  | { kind: 'office' }
  | { kind: 'engineer'; engineerId: string }
  | { kind: 'request'; requestId: string };

export interface MarkerStyle {
  background?: string;
  borderColor?: string;
  color?: string;
}

/** Маркер без привязки к библиотеке карт: координаты, CSS-классы, подпись и подсказка. */
export interface MapMarker {
  key: string;
  target: MarkerTarget;
  coordinates: LngLat;
  zIndex: number;
  className: string;
  style: MarkerStyle;
  title: string;
  label: string;
}

export interface MapPolyline {
  key: string;
  engineerId: string;
  coordinates: LngLat[];
  /** Цвет инженера без прозрачности. */
  color: string;
  /** Цвет линии с учётом затемнения, для отрисовщиков без отдельной прозрачности. */
  stroke: string;
  opacity: number;
  width: number;
}

export interface MapModel {
  markers: MapMarker[];
  polylines: MapPolyline[];
}

export interface MapModelInput {
  state: PlanningState;
  plan: Plan;
  legs: Map<string, RouteLeg[]>;
  showPrevious: boolean;
  selectedRequestId: string | null;
  selectedEngineerId: string | null;
}

/** Всё, что рисуется на карте, в порядке отрисовки: линии маршрутов, офис, старты инженеров, заявки. */
export function buildMapModel({ state, plan, legs, showPrevious, selectedRequestId, selectedEngineerId }: MapModelInput): MapModel {
  const ids = engineerIdsOf(state);
  const assignments = assignmentIndex(plan);
  const marks: Map<string, DiffMark> = showPrevious ? new Map() : diffMarks(state.last_diff);

  const polylines = plan.routes.flatMap((route) => {
    const color = engineerColor(route.engineer_id, ids);
    const dimmed = selectedEngineerId !== null && selectedEngineerId !== route.engineer_id;
    const width = selectedEngineerId === route.engineer_id ? 6 : 3;
    return (legs.get(route.engineer_id) ?? []).map(
      (leg, index): MapPolyline => ({
        key: `${route.engineer_id}-${index}-${leg.to_request_id}`,
        engineerId: route.engineer_id,
        coordinates: leg.coordinates,
        color,
        stroke: dimmed ? `${color}40` : color,
        opacity: dimmed ? DIMMED_ROUTE_OPACITY : 1,
        width,
      }),
    );
  });

  const office: MapMarker = {
    key: 'office',
    target: { kind: 'office' },
    coordinates: [state.office.lon, state.office.lat],
    zIndex: 20,
    className: 'marker marker--office',
    style: {},
    title: state.office.address,
    label: 'Офис',
  };

  const starts = state.engineers.map((engineer): MapMarker => {
    const color = engineerColor(engineer.id, ids);
    const dimmed = !engineer.available || (selectedEngineerId !== null && selectedEngineerId !== engineer.id);
    return {
      key: `start-${engineer.id}`,
      target: { kind: 'engineer', engineerId: engineer.id },
      coordinates: [engineer.start_lon, engineer.start_lat],
      zIndex: 30,
      className: `marker marker--start${dimmed ? ' marker--dimmed' : ''}`,
      style: { borderColor: color, color },
      title: `Старт: ${engineer.name}`,
      label: '⌂',
    };
  });

  const requests = state.requests.flatMap((request): MapMarker[] => {
    if (request.lat === null || request.lon === null) return [];
    const info = assignments.get(request.id);
    const cancelled = request.status === 'cancelled';
    const selected = request.id === selectedRequestId;
    const className = [
      'marker',
      info ? '' : 'marker--unassigned',
      request.priority === 'urgent' ? 'marker--urgent' : '',
      cancelled ? 'marker--cancelled' : '',
      marks.has(request.id) ? 'marker--changed' : '',
      selected ? 'marker--selected' : '',
      selectedEngineerId && info?.engineerId !== selectedEngineerId ? 'marker--dimmed' : '',
    ]
      .filter(Boolean)
      .join(' ');
    return [
      {
        key: request.id,
        target: { kind: 'request', requestId: request.id },
        coordinates: [request.lon, request.lat],
        zIndex: selected ? 100 : 10,
        className,
        style: { background: cancelled ? '#ffffff' : engineerColor(info?.engineerId, ids) },
        title: `${request.id}: ${shortAddress(request.address)}`,
        label: cancelled ? '×' : info ? String(info.order + 1) : '!',
      },
    ];
  });

  return { markers: [office, ...starts, ...requests], polylines };
}
