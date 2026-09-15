import { describe, expect, it } from 'vitest';
import type { RouteLeg } from '../api/types';
import { makePlanningState, makeRouteGeometry } from '../test/fixtures';
import { ENGINEER_PALETTE, UNASSIGNED_COLOR } from './colors';
import { buildMapModel, DIMMED_ROUTE_OPACITY, type MapModelInput } from './mapModel';
import { straightLegs, byId } from './planView';

function input(patch: Partial<MapModelInput> = {}): MapModelInput {
  const state = makePlanningState();
  const requests = byId(state.requests);
  const engineers = byId(state.engineers);
  const legs = new Map<string, RouteLeg[]>();
  for (const route of state.plan.routes) {
    const engineer = engineers.get(route.engineer_id);
    if (engineer && route.visits.length > 0) legs.set(route.engineer_id, straightLegs(route, engineer, requests));
  }
  return { state, plan: state.plan, legs, showPrevious: false, selectedRequestId: null, selectedEngineerId: null, ...patch };
}

const markerOf = (model: ReturnType<typeof buildMapModel>, key: string) => {
  const marker = model.markers.find((item) => item.key === key);
  if (!marker) throw new Error(`нет маркера ${key}`);
  return marker;
};

describe('buildMapModel', () => {
  it('builds office, engineer start and request markers in drawing order', () => {
    const model = buildMapModel(input());
    expect(model.markers.map((marker) => marker.key)).toEqual([
      'office',
      'start-E01',
      'start-E02',
      'start-E03',
      '74198',
      '86160',
      '50104',
      '46393',
      '10135',
      '18754',
      '84627',
      'URG-001',
    ]);
    expect(markerOf(model, 'office')).toEqual({
      key: 'office',
      target: { kind: 'office' },
      coordinates: [37.7862, 55.7075],
      zIndex: 20,
      className: 'marker marker--office',
      style: {},
      title: 'г. Москва, ул Юных Ленинцев, д 83с 4',
      label: 'Офис',
    });
  });

  it('colors engineer starts and dims unavailable engineers', () => {
    const model = buildMapModel(input());
    expect(markerOf(model, 'start-E01')).toEqual({
      key: 'start-E01',
      target: { kind: 'engineer', engineerId: 'E01' },
      coordinates: [37.781, 55.7005],
      zIndex: 30,
      className: 'marker marker--start',
      style: { borderColor: ENGINEER_PALETTE[0], color: ENGINEER_PALETTE[0] },
      title: 'Старт: Бригада Арташкин',
      label: '⌂',
    });
    expect(markerOf(model, 'start-E03').className).toBe('marker marker--start marker--dimmed');
  });

  it('labels requests by order, cancellation and missing assignment', () => {
    const model = buildMapModel(input());
    expect(markerOf(model, 'URG-001')).toMatchObject({
      target: { kind: 'request', requestId: 'URG-001' },
      coordinates: [37.809, 55.712],
      zIndex: 10,
      className: 'marker marker--urgent marker--changed',
      style: { background: ENGINEER_PALETTE[1] },
      label: '2',
    });
    expect(markerOf(model, 'URG-001').title).toMatch(/^URG-001: /);
    expect(markerOf(model, '10135')).toMatchObject({
      className: 'marker marker--unassigned marker--cancelled',
      style: { background: '#ffffff' },
      label: '×',
    });
    expect(markerOf(model, '18754')).toMatchObject({
      className: 'marker marker--unassigned',
      style: { background: UNASSIGNED_COLOR },
      label: '!',
    });
    expect(markerOf(model, '46393')).toMatchObject({ className: 'marker marker--changed', label: '4' });
  });

  it('highlights the selected request and dims everything outside the selected engineer', () => {
    const model = buildMapModel(input({ selectedRequestId: '50104', selectedEngineerId: 'E01' }));
    expect(markerOf(model, '50104')).toMatchObject({ zIndex: 100, className: 'marker marker--changed marker--selected' });
    expect(markerOf(model, '84627').className).toBe('marker marker--dimmed');
    expect(markerOf(model, 'start-E02').className).toBe('marker marker--start marker--dimmed');
    expect(markerOf(model, 'start-E01').className).toBe('marker marker--start');

    const e01 = model.polylines.filter((line) => line.engineerId === 'E01');
    const e02 = model.polylines.filter((line) => line.engineerId === 'E02');
    expect(e01.every((line) => line.width === 6 && line.opacity === 1 && line.stroke === ENGINEER_PALETTE[0])).toBe(true);
    expect(
      e02.every(
        (line) => line.width === 3 && line.opacity === DIMMED_ROUTE_OPACITY && line.stroke === `${ENGINEER_PALETTE[1]}40`,
      ),
    ).toBe(true);
  });

  it('draws one polyline per leg with the engineer color and skips requests without coordinates', () => {
    const base = input();
    const legs = new Map(base.legs);
    legs.set('E02', makeRouteGeometry().legs);
    const requests = base.state.requests.map((request) => (request.id === '86160' ? { ...request, lat: null, lon: null } : request));
    const model = buildMapModel({ ...base, legs, state: { ...base.state, requests } });

    expect(model.markers.some((marker) => marker.key === '86160')).toBe(false);
    expect(model.polylines).toHaveLength(6);
    expect(new Set(model.polylines.map((line) => line.key)).size).toBe(6);
    expect(model.polylines.find((line) => line.engineerId === 'E02')).toEqual({
      key: 'E02-0-84627',
      engineerId: 'E02',
      coordinates: makeRouteGeometry().legs[0].coordinates,
      color: ENGINEER_PALETTE[1],
      stroke: ENGINEER_PALETTE[1],
      opacity: 1,
      width: 3,
    });
  });

  it('does not mark changes while the previous plan is shown', () => {
    const state = makePlanningState();
    const model = buildMapModel(input({ plan: state.previous_plan ?? state.plan, showPrevious: true }));
    expect(model.markers.some((marker) => marker.className.includes('marker--changed'))).toBe(false);
    expect(markerOf(model, 'URG-001')).toMatchObject({ className: 'marker marker--unassigned marker--urgent', label: '!' });
  });
});
