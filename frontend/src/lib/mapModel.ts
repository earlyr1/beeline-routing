import type { HHMM, Plan, PlanningState, RouteLeg } from '../api/types';
import type { LngLat } from '../components/map/yandexLoader';
import { enginePlace, type PhaseKind } from './clock';
import { engineerColor } from './colors';
import { shortAddress } from './format';
import { assignmentIndex, byId, diffMarks, engineerIdsOf, type DiffMark } from './planView';

/** Прозрачность маршрутов других инженеров, когда выбран один: то же, что суффикс `40` у цвета. */
export const DIMMED_ROUTE_OPACITY = 0x40 / 0xff;

/** Прозрачность отрезка, который инженер уже проехал к времени на часах. */
export const PASSED_ROUTE_OPACITY = 0x40 / 0xff;

/** Маркеры «где сейчас» выше заявок (100), но ниже меню карты (1000). */
export const NOW_MARKER_Z_INDEX = 200;

/** Цвет с прозрачностью суффиксом, «#2563eb40»: отрисовщики без отдельного канала прозрачности понимают только его. */
export function alphaColor(color: string, opacity: number): string {
  if (opacity >= 1) return color;
  const alpha = Math.max(0, Math.min(255, Math.round(opacity * 255)));
  return `${color}${alpha.toString(16).padStart(2, '0')}`;
}

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
  /** Заявка, к которой ведёт отрезок: по ней часы дня узнают проеханные отрезки. */
  toRequestId: string;
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
        toRequestId: leg.to_request_id,
        coordinates: leg.coordinates,
        color,
        stroke: alphaColor(color, dimmed ? DIMMED_ROUTE_OPACITY : 1),
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

/** Значок фазы на маркере «где сейчас». */
const PHASE_GLYPHS: Record<PhaseKind, string> = {
  before: '⏸',
  driving: '→',
  waiting: '⌛',
  onSite: '●',
  lunch: '☕',
  done: '✓',
};

/** Отрезки маршрутов глазами часов дня: ключ отрезка — инженер и заявка, к которой он ведёт. */
export interface ClockLegs {
  /** Отрезки, которые инженер уже проехал: они рисуются бледнее. */
  passed: ReadonlySet<string>;
  /** Отрезки, по которым инженеры едут сейчас: их рисует слой часов, по две линии на отрезок. */
  split: ReadonlySet<string>;
}

export const NO_CLOCK_LEGS: ClockLegs = { passed: new Set(), split: new Set() };

export const legKey = (engineerId: string, requestId: string): string => `${engineerId}:${requestId}`;

export interface ClockLayerInput {
  state: PlanningState;
  plan: Plan;
  legs: Map<string, RouteLeg[]>;
  clock: HHMM;
  selectedEngineerId: string | null;
}

/** Слой часов дня: маркеры «где сейчас», разрезанные текущие отрезки и список проеханных отрезков. */
export interface ClockLayer {
  markers: MapMarker[];
  polylines: MapPolyline[];
  legs: ClockLegs;
}

/** Где инженеры в момент на часах: маркер каждому, у кого есть визиты, и текущий отрезок пути, разрезанный этой точкой. */
export function buildClockLayer({ state, plan, legs, clock, selectedEngineerId }: ClockLayerInput): ClockLayer {
  const ids = engineerIdsOf(state);
  const requests = byId(state.requests);
  const markers: MapMarker[] = [];
  const polylines: MapPolyline[] = [];
  const passed = new Set<string>();
  const split = new Set<string>();

  for (const engineer of state.engineers) {
    const route = plan.routes.find((item) => item.engineer_id === engineer.id);
    const place = enginePlace({ engineer, route, requests, legs: legs.get(engineer.id) ?? [], clock });
    if (!place) continue;
    for (const requestId of place.driven) passed.add(legKey(engineer.id, requestId));
    const color = engineerColor(engineer.id, ids);
    const dimmed = selectedEngineerId !== null && selectedEngineerId !== engineer.id;
    const dim = dimmed ? DIMMED_ROUTE_OPACITY : 1;
    const width = selectedEngineerId === engineer.id ? 6 : 3;

    if (place.split) {
      split.add(legKey(engineer.id, place.split.requestId));
      const part = (kind: 'passed' | 'rest', coordinates: LngLat[], opacity: number): MapPolyline => ({
        key: `now-${kind}-${engineer.id}`,
        engineerId: engineer.id,
        toRequestId: place.split?.requestId ?? '',
        coordinates,
        color,
        stroke: alphaColor(color, opacity),
        opacity,
        width,
      });
      // Части короче двух точек не рисуются: в начале пути нет проеханной части, в конце — оставшейся.
      if (place.split.passed.length > 1) polylines.push(part('passed', place.split.passed, PASSED_ROUTE_OPACITY * dim));
      if (place.split.rest.length > 1) polylines.push(part('rest', place.split.rest, dim));
    }

    if (!place.point) continue;
    markers.push({
      key: `now-${engineer.id}`,
      target: { kind: 'engineer', engineerId: engineer.id },
      coordinates: place.point,
      zIndex: NOW_MARKER_Z_INDEX,
      className: `marker marker--now marker--now-${place.phase.kind}${dimmed ? ' marker--dimmed' : ''}`,
      style: { background: color, borderColor: color },
      title: `${engineer.name}: ${place.phase.text}`,
      label: PHASE_GLYPHS[place.phase.kind],
    });
  }

  return { markers, polylines, legs: { passed, split } };
}

/** Базовые линии с учётом часов: проеханные бледнее, а текущий отрезок убран — его рисует слой часов. */
export function withClockLegs(polylines: MapPolyline[], legs: ClockLegs): MapPolyline[] {
  if (legs.passed.size === 0 && legs.split.size === 0) return polylines;
  return polylines.flatMap((line): MapPolyline[] => {
    const key = legKey(line.engineerId, line.toRequestId);
    if (legs.split.has(key)) return [];
    if (!legs.passed.has(key)) return [line];
    const opacity = line.opacity * PASSED_ROUTE_OPACITY;
    return [{ ...line, opacity, stroke: alphaColor(line.color, opacity) }];
  });
}
