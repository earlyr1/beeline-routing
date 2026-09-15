import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  addTimelineEvent,
  ApiError,
  buildPlan,
  deleteTimelineEvent,
  getReverseGeocode,
  getRouteGeometry,
  moveCursor,
  postEvent,
  uploadFile,
} from './client';
import type { PlanEvent } from './types';

const cancel: PlanEvent = { type: 'cancel', time: '13:00', request: null, request_id: '50104', engineer_id: null };

const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as unknown as Response;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api client', () => {
  it('uploads the file as multipart form data', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(202, { dataset_id: 'd1', status: 'processing' }));
    vi.stubGlobal('fetch', fetchMock);
    const result = await uploadFile(new File(['a;b'], 'east.csv', { type: 'text/csv' }));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(result.dataset_id).toBe('d1');
    expect(url).toBe('/api/upload');
    expect(init.method).toBe('POST');
    expect((init.body as FormData).get('file')).toBeInstanceOf(File);
  });

  it('posts events as JSON to the dataset', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { version: 2 }));
    vi.stubGlobal('fetch', fetchMock);
    await postEvent('d 1', cancel);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/datasets/d%201/events');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body as string)).toEqual(cancel);
  });

  it('adds an event to the timeline of the day as JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { version: 2 }));
    vi.stubGlobal('fetch', fetchMock);
    await addTimelineEvent('d 1', cancel);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/datasets/d%201/timeline/events');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body as string)).toEqual(cancel);
  });

  it('deletes a timeline event by its id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { version: 2 }));
    vi.stubGlobal('fetch', fetchMock);
    await deleteTimelineEvent('d1', 'tl/1');
    expect(fetchMock.mock.calls[0]).toEqual(['/api/datasets/d1/timeline/events/tl%2F1', { method: 'DELETE' }]);
  });

  it('moves the plan cursor to a time of the day', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { version: 2 }));
    vi.stubGlobal('fetch', fetchMock);
    await moveCursor('d1', '14:05');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/datasets/d1/cursor');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ time: '14:05' });
  });

  it('sends the workload level and the lunch with the plan request only when they are given', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { version: 2 }));
    vi.stubGlobal('fetch', fetchMock);
    await buildPlan('d 1', { workload_level: 0, lunch: false });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/datasets/d%201/plan');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body as string)).toEqual({ workload_level: 0, lunch: false });

    await buildPlan('d1', { lunch: true });
    expect(JSON.parse((fetchMock.mock.calls[1] as [string, RequestInit])[1].body as string)).toEqual({ lunch: true });

    await buildPlan('d1');
    expect(fetchMock.mock.calls[2]).toEqual(['/api/datasets/d1/plan', { method: 'POST' }]);
    await buildPlan('d1', {});
    expect(fetchMock.mock.calls[3]).toEqual(['/api/datasets/d1/plan', { method: 'POST' }]);
  });

  it('passes the plan kind to the geometry endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { legs: [] }));
    vi.stubGlobal('fetch', fetchMock);
    await getRouteGeometry('d1', 'E01', 'previous');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/datasets/d1/routes/E01/geometry?plan=previous');
  });

  it('asks the reverse geocoder for the address of a map point', async () => {
    const address = { address: 'Москва, Перовская улица, 42к1', precision: 'house' };
    const fetchMock = vi.fn().mockResolvedValue(reply(200, address));
    vi.stubGlobal('fetch', fetchMock);
    await expect(getReverseGeocode(55.75123, 37.78)).resolves.toEqual(address);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/geocode/reverse?lat=55.75123&lon=37.78');
  });

  it('turns backend detail into ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(409, { detail: 'Датасет ещё обрабатывается' })));
    await expect(buildPlan('d1')).rejects.toMatchObject({ status: 409, message: 'Датасет ещё обрабатывается' });
  });

  it('joins FastAPI validation errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(422, { detail: [{ msg: 'Field required' }, { msg: 'bad time' }] })));
    await expect(buildPlan('d1')).rejects.toMatchObject({ status: 422, message: 'Field required; bad time' });
  });

  it('reports a network failure as ApiError with status 0', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const error = await buildPlan('d1').catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0 });
  });
});
