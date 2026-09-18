import { describe, expect, it } from 'vitest';
import type { RouteLeg } from '../api/types';
import { makeDataUrgentState, makePlanningState, makeRouteGeometry } from '../test/fixtures';
import { ENGINEER_PALETTE, UNASSIGNED_COLOR } from './colors';
import {
  buildClockLayer,
  buildMapModel,
  DIMMED_ROUTE_OPACITY,
  isClickableMarker,
  legKey,
  NO_CLOCK_LEGS,
  NOW_MARKER_Z_INDEX,
  PASSED_ROUTE_OPACITY,
  withClockLegs,
  type ClockLayerInput,
  type MapModelInput,
} from './mapModel';
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

  it('makes only request markers clickable', () => {
    const model = buildMapModel(input());
    const clock = buildClockLayer(clockInput());
    expect(isClickableMarker(markerOf(model, 'start-E01'))).toBe(false);
    expect(isClickableMarker(markerOf(model, 'office'))).toBe(false);
    expect(clock.markers.length).toBeGreaterThan(0);
    for (const marker of clock.markers) expect(isClickableMarker(marker)).toBe(false);
    const requests = model.markers.filter((marker) => marker.target.kind === 'request');
    expect(requests.length).toBeGreaterThan(0);
    for (const marker of requests) expect(isClickableMarker(marker)).toBe(true);
  });

  it('names an urgent request of the day with the URG- prefix and keeps its raw number as the key', () => {
    const state = makeDataUrgentState();
    const model = buildMapModel(input({ state, plan: state.plan }));
    expect(markerOf(model, '50104')).toMatchObject({
      target: { kind: 'request', requestId: '50104' },
      title: 'URG-50104: ул.Грайвороновская, д. 10 к 2',
    });
    // Заявка диспетчера приходит уже с приставкой, обычная заявка остаётся со своим номером.
    expect(markerOf(model, 'URG-001').title).toMatch(/^URG-001: /);
    expect(markerOf(model, '46393').title).toMatch(/^46393: /);
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
      toRequestId: '84627',
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

function clockInput(patch: Partial<ClockLayerInput> = {}): ClockLayerInput {
  const base = input();
  return { state: base.state, plan: base.plan, legs: base.legs, clock: '13:20', selectedEngineerId: null, ...patch };
}

describe('buildClockLayer', () => {
  it('ставит маркер «где сейчас» каждому инженеру с визитами и никому без них', () => {
    const layer = buildClockLayer(clockInput());
    expect(layer.markers.map((marker) => marker.key)).toEqual(['now-E01', 'now-E02']);
    expect(layer.markers[0]).toMatchObject({
      target: { kind: 'engineer', engineerId: 'E01' },
      zIndex: NOW_MARKER_Z_INDEX,
      className: 'marker marker--now marker--now-driving',
      style: { background: ENGINEER_PALETTE[0], borderColor: ENGINEER_PALETTE[0] },
      title: 'Бригада Арташкин: в пути к 50104',
    });
    // Инженер едет от 86160 к 50104 и проехал 20 минут из 35.
    expect(layer.markers[0].coordinates[0]).toBeCloseTo(37.7026, 3);
    expect(layer.markers[0].coordinates[1]).toBeCloseTo(55.7306, 3);
    expect(layer.markers[1]).toMatchObject({
      key: 'now-E02',
      className: 'marker marker--now marker--now-onSite',
      title: 'Бригада Белузин: работает у URG-001',
      coordinates: [37.809, 55.712],
    });
  });

  it('режет отрезок, по которому инженер едет, на проеханную и оставшуюся части', () => {
    const layer = buildClockLayer(clockInput());
    expect(layer.polylines.map((line) => line.key)).toEqual(['now-passed-E01', 'now-rest-E01']);
    const [passed, rest] = layer.polylines;
    expect(passed.coordinates[0]).toEqual([37.6612, 55.7431]);
    expect(passed.coordinates.at(-1)).toEqual(rest.coordinates[0]);
    expect(rest.coordinates.at(-1)).toEqual([37.7336, 55.7212]);
    expect(passed.opacity).toBeCloseTo(PASSED_ROUTE_OPACITY, 6);
    expect(passed.stroke).toBe(`${ENGINEER_PALETTE[0]}40`);
    expect(rest).toMatchObject({ engineerId: 'E01', opacity: 1, stroke: ENGINEER_PALETTE[0], width: 3 });
  });

  it('в начале пути рисует только оставшуюся часть отрезка', () => {
    // В 13:00 инженер только выезжает к 50104: приезд 13:35 минус 35 минут дороги.
    const layer = buildClockLayer(clockInput({ clock: '13:00' }));
    expect(layer.polylines.map((line) => line.key)).toEqual(['now-rest-E01', 'now-passed-E02', 'now-rest-E02']);
  });

  it('называет проеханные отрезки и текущий: базовые линии рисуются по ним', () => {
    const layer = buildClockLayer(clockInput());
    expect([...layer.legs.passed].sort()).toEqual(['E01:74198', 'E01:86160', 'E02:84627', 'E02:URG-001']);
    expect([...layer.legs.split]).toEqual([legKey('E01', '50104')]);
  });

  it('затемняет маркер и линии инженеров, которых не выбрали', () => {
    const layer = buildClockLayer(clockInput({ selectedEngineerId: 'E01' }));
    expect(layer.markers[1].className).toBe('marker marker--now marker--now-onSite marker--dimmed');
    expect(layer.markers[0].className).toBe('marker marker--now marker--now-driving');
    expect(layer.polylines.every((line) => line.width === 6)).toBe(true);
  });
});

describe('withClockLegs', () => {
  it('гасит проеханные отрезки и убирает тот, который рисует слой часов', () => {
    const model = buildMapModel(input());
    const layer = buildClockLayer(clockInput());
    const lines = withClockLegs(model.polylines, layer.legs);

    expect(model.polylines).toHaveLength(6);
    expect(lines.map((line) => line.key)).toEqual(['E01-0-74198', 'E01-1-86160', 'E01-3-46393', 'E02-0-84627', 'E02-1-URG-001']);
    const passed = lines.filter((line) => line.key !== 'E01-3-46393');
    for (const line of passed) {
      expect(line.opacity).toBeCloseTo(PASSED_ROUTE_OPACITY, 6);
      expect(line.stroke).toBe(`${line.color}40`);
    }
    expect(lines.find((line) => line.key === 'E01-3-46393')).toMatchObject({ opacity: 1, stroke: ENGINEER_PALETTE[0] });
  });

  it('перемножает прозрачность проеханного отрезка и затемнения чужого маршрута', () => {
    const model = buildMapModel(input({ selectedEngineerId: 'E01' }));
    const layer = buildClockLayer(clockInput({ selectedEngineerId: 'E01' }));
    const line = withClockLegs(model.polylines, layer.legs).find((item) => item.engineerId === 'E02');
    expect(line?.opacity).toBeCloseTo(PASSED_ROUTE_OPACITY * DIMMED_ROUTE_OPACITY, 6);
    expect(line?.stroke).toBe(`${ENGINEER_PALETTE[1]}10`);
  });

  it('без часов оставляет линии как есть', () => {
    const model = buildMapModel(input());
    expect(withClockLegs(model.polylines, NO_CLOCK_LEGS)).toBe(model.polylines);
  });
});
