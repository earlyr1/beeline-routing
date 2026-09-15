import { describe, expect, it } from 'vitest';
import { makePlanningState } from '../test/fixtures';
import { overviewBounds, toLatLng, toLatLngBounds } from './mapView';

describe('Leaflet coordinates', () => {
  it('puts latitude first', () => {
    expect(toLatLng([37.7862, 55.7075])).toEqual([55.7075, 37.7862]);
  });

  it('turns top-left and bottom-right corners into south-west and north-east', () => {
    expect(
      toLatLngBounds([
        [37.6, 55.8],
        [37.9, 55.6],
      ]),
    ).toEqual([
      [55.6, 37.6],
      [55.8, 37.9],
    ]);
  });
});

describe('overviewBounds', () => {
  it('covers office, engineer starts and requests with top-left and bottom-right corners', () => {
    const state = makePlanningState();
    const [[left, top], [right, bottom]] = overviewBounds(state);
    const points = [
      [state.office.lon, state.office.lat],
      ...state.engineers.map((engineer) => [engineer.start_lon, engineer.start_lat]),
      ...state.requests.filter((request) => request.lat !== null && request.lon !== null).map((request) => [request.lon, request.lat]),
    ] as [number, number][];
    expect(left).toBeLessThan(right);
    expect(top).toBeGreaterThan(bottom);
    for (const [lon, lat] of points) {
      expect(lon).toBeGreaterThan(left);
      expect(lon).toBeLessThan(right);
      expect(lat).toBeGreaterThan(bottom);
      expect(lat).toBeLessThan(top);
    }
  });

  it('keeps a minimal area around a single point', () => {
    const base = makePlanningState();
    const state = { ...base, engineers: [], requests: [] };
    const [[left, top], [right, bottom]] = overviewBounds(state);
    expect(right - left).toBeCloseTo(0.02, 5);
    expect(top - bottom).toBeCloseTo(0.02, 5);
  });
});
