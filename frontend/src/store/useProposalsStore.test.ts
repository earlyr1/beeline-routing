import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    sendChat: vi.fn(),
    getProposals: vi.fn(),
    approveProposal: vi.fn(),
    rejectProposal: vi.fn(),
    approveAllProposals: vi.fn(),
    rejectAllProposals: vi.fn(),
    moveCursor: vi.fn(),
  };
});

import * as api from '../api/client';
import type { ApproveResponse } from '../api/types';
import { makePlanningState } from '../test/fixtures';
import { makeProposal, makeUrgentProposal } from '../test/proposalFixtures';
import { resetStore } from '../test/store';
import { useAppStore } from './useAppStore';
import { initialProposalsData, useProposalsStore } from './useProposalsStore';

const pristine = useProposalsStore.getState();
const NOT_UNDERSTOOD =
  'Не понял: инженер «Кузнецов» не найден. Напишите сообщение целиком ещё раз — прошлых сообщений помощник не помнит.';

beforeEach(() => {
  vi.resetAllMocks();
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
  useProposalsStore.setState({ ...pristine, ...initialProposalsData }, true);
});

describe('useProposalsStore', () => {
  it('sends a message and keeps proposals and the not-understood answer', async () => {
    vi.mocked(api.sendChat).mockResolvedValue({ proposals: [makeProposal()], clarification: NOT_UNDERSTOOD });
    expect(await useProposalsStore.getState().send('Отмена на Грайвороновской')).toBe(true);
    expect(api.sendChat).toHaveBeenCalledWith('d_test', 'Отмена на Грайвороновской');
    expect(useProposalsStore.getState()).toMatchObject({ clarification: NOT_UNDERSTOOD, sending: false, datasetId: 'd_test' });
    expect(useProposalsStore.getState().proposals.map((item) => item.id)).toEqual(['pr_1']);
  });

  it('approves through the backend and refreshes the shared planning state', async () => {
    useProposalsStore.setState({ datasetId: 'd_test', proposals: [makeProposal()] });
    const state = makePlanningState({ version: 5 });
    vi.mocked(api.approveProposal).mockResolvedValue({
      proposal: makeProposal({ status: 'approved', result_diff: state.last_diff }),
      state,
    });
    await useProposalsStore.getState().approve('pr_1');
    expect(api.approveProposal).toHaveBeenCalledWith('d_test', 'pr_1');
    expect(useProposalsStore.getState().proposals[0].status).toBe('approved');
    expect(useAppStore.getState().state?.version).toBe(5);
    expect(useProposalsStore.getState().working).toBe(false);
  });

  it('stops the playback without committing and puts the clock at the cursor of the approved plan', async () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '14:20', playing: true });
    useProposalsStore.setState({ datasetId: 'd_test', proposals: [makeProposal()] });
    vi.mocked(api.approveProposal).mockResolvedValue({
      proposal: makeProposal({ status: 'approved' }),
      state: makePlanningState({ version: 5, cursor: '14:30' }),
    });
    await useProposalsStore.getState().approve('pr_1');
    expect(useAppStore.getState()).toMatchObject({ playing: false, clock: '14:30' });
    expect(useAppStore.getState().state?.version).toBe(5);
    expect(api.moveCursor).not.toHaveBeenCalled();
  });

  it('does not bring back the old plan after the dispatcher switched to another file', async () => {
    useProposalsStore.setState({ datasetId: 'd_test', proposals: [makeProposal()] });
    let resolve!: (value: ApproveResponse) => void;
    vi.mocked(api.approveProposal).mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const pending = useProposalsStore.getState().approve('pr_1');
    useAppStore.getState().reset();
    resolve({ proposal: makeProposal({ status: 'approved' }), state: makePlanningState({ version: 5 }) });
    await pending;
    expect(useAppStore.getState().state).toBeNull();
  });

  it('reloads the list when a proposal was already processed', async () => {
    useProposalsStore.setState({ datasetId: 'd_test', proposals: [makeProposal()] });
    vi.mocked(api.approveProposal).mockRejectedValue(new api.ApiError(409, 'Предложение pr_1 уже применено.'));
    vi.mocked(api.getProposals).mockResolvedValue([makeProposal({ status: 'approved' })]);
    await useProposalsStore.getState().approve('pr_1');
    expect(useProposalsStore.getState()).toMatchObject({ error: 'Предложение pr_1 уже применено.' });
    expect(useProposalsStore.getState().proposals[0].status).toBe('approved');
  });

  it('approves all, rejects one and rejects all', async () => {
    useProposalsStore.setState({ datasetId: 'd_test', proposals: [makeProposal(), makeUrgentProposal()] });
    vi.mocked(api.rejectProposal).mockResolvedValue(makeProposal({ status: 'rejected' }));
    await useProposalsStore.getState().reject('pr_1');
    expect(useProposalsStore.getState().proposals.map((item) => item.status)).toEqual(['rejected', 'pending']);

    const state = makePlanningState({ version: 6 });
    vi.mocked(api.approveAllProposals).mockResolvedValue({
      proposals: [makeProposal({ status: 'rejected' }), makeUrgentProposal({ status: 'failed', error: 'Окно уже прошло' })],
      state,
    });
    await useProposalsStore.getState().approveAll();
    expect(useProposalsStore.getState().proposals[1].error).toBe('Окно уже прошло');
    expect(useAppStore.getState().state?.version).toBe(6);

    vi.mocked(api.rejectAllProposals).mockResolvedValue([makeProposal({ status: 'rejected' })]);
    await useProposalsStore.getState().rejectAll();
    expect(useProposalsStore.getState().proposals).toHaveLength(1);
  });

  it('resets the list when the dataset changes', async () => {
    useProposalsStore.setState({ datasetId: 'd_old', proposals: [makeProposal()], clarification: 'старое' });
    vi.mocked(api.getProposals).mockResolvedValue([]);
    await useProposalsStore.getState().load('d_test');
    expect(useProposalsStore.getState()).toMatchObject({ datasetId: 'd_test', proposals: [], clarification: null });
  });
});
