import { describe, expect, it } from 'vitest';
import type { Engineer, Route, RouteLeg, ServiceRequest } from '../api/types';
import { makeDataUrgentState, makePlanningState, makeRouteGeometry } from '../test/fixtures';
import { enginePhase, enginePlace, pointAlong, requestClockStatus, splitAlong, type PlaceInput } from './clock';
import { byId, straightLegs } from './planView';

const state = makePlanningState();
const requests = byId(state.requests);
const engineers = byId(state.engineers);

const engineerOf = (engineerId: string): Engineer => {
  const engineer = engineers.get(engineerId);
  if (!engineer) throw new Error(`нет инженера ${engineerId}`);
  return engineer;
};

const routeOf = (engineerId: string): Route | undefined => state.plan.routes.find((route) => route.engineer_id === engineerId);

const phase = (engineerId: string, clock: string) => enginePhase(routeOf(engineerId), clock, requests);

function place(engineerId: string, clock: string, patch: Partial<PlaceInput> = {}) {
  const engineer = engineerOf(engineerId);
  const route = routeOf(engineerId);
  const legs = route ? straightLegs(route, engineer, requests) : [];
  return enginePlace({ engineer, route, requests, legs, clock, ...patch });
}

const visitOf = (engineerId: string, requestId: string) =>
  routeOf(engineerId)?.visits.find((visit) => visit.request_id === requestId);

describe('pointAlong и splitAlong', () => {
  // Отрезки разной длины: доля пути считается по длине, а не по числу точек.
  const line: [number, number][] = [
    [37, 55],
    [37, 55.2],
    [37, 55.6],
  ];

  it('ставит точку по доле длины ломаной, а не по номеру отрезка', () => {
    expect(pointAlong(line, 0)).toEqual([37, 55]);
    expect(pointAlong(line, 1)).toEqual([37, 55.6]);
    const half = pointAlong(line, 0.5);
    expect(half?.[0]).toBeCloseTo(37, 6);
    // Половина длины 0,6 — это 0,3: конец первого отрезка плюс четверть второго.
    expect(half?.[1]).toBeCloseTo(55.3, 6);
    expect(pointAlong(line, 1 / 3)?.[1]).toBeCloseTo(55.2, 6);
  });

  it('учитывает сжатие долготы к северу: градус долготы короче градуса широты', () => {
    const corner: [number, number][] = [
      [37, 55],
      [38, 55],
    ];
    // Градус долготы на широте 55 примерно вдвое короче градуса широты, но доля пути по одному отрезку от этого не зависит.
    expect(pointAlong(corner, 0.25)?.[0]).toBeCloseTo(37.25, 6);
  });

  it('оставляет точку на месте у вырожденной ломаной и за пределами 0..1', () => {
    expect(pointAlong([[37, 55]], 0.5)).toEqual([37, 55]);
    expect(pointAlong([], 0.5)).toBeNull();
    expect(
      pointAlong(
        [
          [37, 55],
          [37, 55],
        ],
        0.5,
      ),
    ).toEqual([37, 55]);
    expect(pointAlong(line, -1)).toEqual([37, 55]);
    expect(pointAlong(line, 2)).toEqual([37, 55.6]);
  });

  it('режет ломаную в точке доли пути на проеханную и оставшуюся части', () => {
    const { passed, rest } = splitAlong(line, 0.5);
    expect(passed).toHaveLength(3);
    expect(passed[0]).toEqual([37, 55]);
    expect(passed[1]).toEqual([37, 55.2]);
    expect(passed[2][1]).toBeCloseTo(55.3, 6);
    expect(rest).toHaveLength(2);
    expect(rest[0][1]).toBeCloseTo(55.3, 6);
    expect(rest[1]).toEqual([37, 55.6]);
  });

  it('в начале пути не рисует проеханную часть, в конце — оставшуюся', () => {
    expect(splitAlong(line, 0)).toEqual({ passed: [], rest: line });
    expect(splitAlong(line, 1)).toEqual({ passed: line, rest: [] });
  });
});

describe('enginePhase', () => {
  it('до первого выезда говорит, что инженер ещё не выехал', () => {
    expect(phase('E01', '08:30')).toEqual({ kind: 'before', requestId: '74198', text: 'ещё не выехал', progress: null });
  });

  it('в пути считает долю проеханного времени от выезда до приезда', () => {
    // Выезд к 50104 в 13:00: приезд 13:35 минус 35 минут дороги.
    expect(phase('E01', '13:00')).toEqual({ kind: 'driving', requestId: '50104', text: 'в пути к 50104', progress: 0 });
    const middle = phase('E01', '13:20');
    expect(middle).toMatchObject({ kind: 'driving', requestId: '50104' });
    expect(middle?.progress).toBeCloseTo(20 / 35, 6);
    expect(phase('E02', '13:00')?.progress).toBeCloseTo(0.8, 6);
  });

  it('ждёт у клиента между приездом и началом работ', () => {
    expect(phase('E01', '09:50')).toEqual({
      kind: 'waiting',
      requestId: '74198',
      text: 'ждёт у клиента 74198',
      progress: null,
    });
  });

  it('работает у клиента от начала работ до их конца', () => {
    expect(phase('E01', '10:30')).toEqual({ kind: 'onSite', requestId: '74198', text: 'работает у 74198', progress: null });
    // Конец работ уже относится к следующему визиту: в 13:00 инженер выезжает к 50104.
    expect(phase('E01', '13:00')?.kind).toBe('driving');
  });

  it('в подсказке номер срочной заявки дня идёт с приставкой URG-, а сама фаза держит сырой номер', () => {
    const urgent = byId(makeDataUrgentState().requests);
    const driving = enginePhase(routeOf('E01'), '13:00', urgent);
    expect(driving).toEqual({ kind: 'driving', requestId: '50104', text: 'в пути к URG-50104', progress: 0 });
    expect(enginePhase(routeOf('E01'), '14:30', urgent)?.text).toBe('работает у URG-50104');
    // Обычная заявка остаётся со своим номером.
    expect(enginePhase(routeOf('E01'), '10:30', urgent)?.text).toBe('работает у 74198');
  });

  it('обед главнее дороги и работы', () => {
    expect(phase('E01', '16:00')).toEqual({ kind: 'lunch', requestId: null, text: 'обед', progress: null });
    expect(phase('E02', '14:30')?.kind).toBe('lunch');
    expect(phase('E02', '14:50')?.kind).not.toBe('lunch');
  });

  it('после последних работ день закончен', () => {
    expect(phase('E01', '17:00')).toEqual({ kind: 'done', requestId: '46393', text: 'работы закончены', progress: null });
  });

  it('ждёт выезда, когда между работами есть пауза', () => {
    const route: Route = {
      engineer_id: 'E01',
      visits: [
        { request_id: 'A', arrival: '10:00', start: '10:00', end: '11:00', leg_km: 1, leg_min: 10, late_min: 0, pinned: false },
        { request_id: 'B', arrival: '13:00', start: '13:00', end: '14:00', leg_km: 1, leg_min: 10, late_min: 0, pinned: false },
      ],
      total_km: 2,
      total_travel_min: 20,
      lunch: null,
    };
    expect(enginePhase(route, '12:00', requests)).toEqual({ kind: 'before', requestId: 'B', text: 'ждёт выезда к B', progress: null });
    expect(enginePhase(route, '12:50', requests)?.kind).toBe('driving');
  });

  it('без маршрута и без визитов фазы нет', () => {
    expect(phase('E03', '13:00')).toBeNull();
    expect(enginePhase(undefined, '13:00', requests)).toBeNull();
  });
});

describe('enginePlace', () => {
  it('до выезда ставит инженера на точку старта, а в конце дня — у последнего клиента', () => {
    expect(place('E01', '08:30')?.point).toEqual([37.781, 55.7005]);
    expect(place('E01', '17:00')?.point).toEqual([37.68, 55.7195]);
  });

  it('у клиента и в ожидании ставит инженера в точку заявки', () => {
    expect(place('E01', '10:30')?.point).toEqual([37.7822, 55.7008]);
    expect(place('E01', '09:50')?.point).toEqual([37.7822, 55.7008]);
  });

  it('на обеде ставит инженера туда, где он освободился', () => {
    // Обед E01 в 15:55–16:40 начинается сразу после работ у 46393.
    expect(place('E01', '16:00')?.point).toEqual([37.68, 55.7195]);
  });

  it('обед перед первым визитом проходит в точке старта', () => {
    const route: Route = {
      engineer_id: 'E01',
      visits: [
        { request_id: '50104', arrival: '12:00', start: '12:00', end: '13:00', leg_km: 1, leg_min: 10, late_min: 0, pinned: false },
      ],
      total_km: 1,
      total_travel_min: 10,
      lunch: { start: '10:00', end: '10:45' },
    };
    expect(place('E01', '10:20', { route, legs: [] })?.point).toEqual([37.781, 55.7005]);
  });

  it('в пути ставит инженера на линию маршрута по доле времени', () => {
    const legs: RouteLeg[] = makeRouteGeometry().legs;
    const driving = place('E02', '13:00', { legs });
    expect(driving?.phase.kind).toBe('driving');
    // Отрезок к URG-001 из геометрии: [37.805, 55.76] → [37.809, 55.712], доля 0,8.
    expect(driving?.point?.[0]).toBeCloseTo(37.8082, 4);
    expect(driving?.point?.[1]).toBeCloseTo(55.7216, 4);
    expect(driving?.split?.requestId).toBe('URG-001');
    expect(driving?.split?.passed[0]).toEqual([37.805, 55.76]);
    expect(driving?.split?.rest.at(-1)).toEqual([37.809, 55.712]);
  });

  it('называет отрезки, к которым инженер уже доехал', () => {
    expect(place('E01', '13:20')?.driven).toEqual(['74198', '86160']);
    expect(place('E01', '17:00')?.driven).toEqual(['74198', '86160', '50104', '46393']);
    expect(place('E01', '08:30')?.driven).toEqual([]);
  });

  it('без координат заявки оставляет инженера в предыдущей точке и не режет отрезок', () => {
    const blind = new Map<string, ServiceRequest>(requests);
    const request = blind.get('50104');
    if (request) blind.set('50104', { ...request, lat: null, lon: null });
    const driving = place('E01', '13:20', { requests: blind, legs: [] });
    expect(driving?.phase.kind).toBe('driving');
    expect(driving?.point).toEqual([37.6612, 55.7431]);
    expect(driving?.split).toBeNull();
  });

  it('без маршрута места нет', () => {
    expect(place('E03', '13:00')).toBeNull();
  });
});

describe('requestClockStatus', () => {
  it('различает выполненную, начатую и заявку в пути', () => {
    const visit = visitOf('E01', '50104');
    expect(requestClockStatus(visit, '13:10')).toBe('driving');
    expect(requestClockStatus(visit, '14:10')).toBe('working');
    expect(requestClockStatus(visit, '14:45')).toBe('done');
  });

  it('до выезда и в ожидании у клиента статуса нет', () => {
    const visit = visitOf('E01', '50104');
    expect(requestClockStatus(visit, '12:30')).toBeNull();
    // Приезд 13:35, начало работ 14:00: заявка ещё не в работе и уже не в пути.
    expect(requestClockStatus(visit, '13:40')).toBeNull();
    expect(requestClockStatus(undefined, '13:40')).toBeNull();
  });
});
