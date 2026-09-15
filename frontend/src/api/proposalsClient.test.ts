import { afterEach, describe, expect, it, vi } from 'vitest';
import { approveAllProposals, approveProposal, getProposals, rejectAllProposals, rejectProposal, sendChat } from './client';

const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as unknown as Response;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('proposals api client', () => {
  it('posts the chat text as JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { proposals: [], clarification: null }));
    vi.stubGlobal('fetch', fetchMock);
    await sendChat('d1', 'Арташкин заболел');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/datasets/d1/chat');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ text: 'Арташкин заболел' });
  });

  it('uses the proposal endpoints from the contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    await getProposals('d1');
    await approveProposal('d1', 'pr 1');
    await rejectProposal('d1', 'pr_2');
    await approveAllProposals('d1');
    await rejectAllProposals('d1');
    expect(fetchMock.mock.calls.map((call) => [call[0], (call[1] as RequestInit | undefined)?.method ?? 'GET'])).toEqual([
      ['/api/datasets/d1/proposals', 'GET'],
      ['/api/datasets/d1/proposals/pr%201/approve', 'POST'],
      ['/api/datasets/d1/proposals/pr_2/reject', 'POST'],
      ['/api/datasets/d1/proposals/approve-all', 'POST'],
      ['/api/datasets/d1/proposals/reject-all', 'POST'],
    ]);
  });

  it('surfaces the 503 detail when the assistant is not configured', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(503, { detail: 'Помощник не настроен' })));
    await expect(sendChat('d1', 'x')).rejects.toMatchObject({ status: 503, message: 'Помощник не настроен' });
  });
});
