import { create } from 'zustand';
import {
  ApiError,
  approveAllProposals,
  approveProposal,
  getProposals,
  rejectAllProposals,
  rejectProposal,
  sendChat,
} from '../api/client';
import type { PlanningState, Proposal } from '../api/types';
import { upsertProposal } from '../lib/proposals';
import { useAppStore } from './useAppStore';

export interface ProposalsData {
  datasetId: string | null;
  proposals: Proposal[];
  clarification: string | null;
  sending: boolean;
  working: boolean;
  error: string | null;
}

export interface ProposalsActions {
  load(datasetId: string): Promise<void>;
  send(text: string): Promise<boolean>;
  approve(proposalId: string): Promise<void>;
  reject(proposalId: string): Promise<void>;
  approveAll(): Promise<void>;
  rejectAll(): Promise<void>;
}

export type ProposalsState = ProposalsData & ProposalsActions;

export const initialProposalsData: ProposalsData = {
  datasetId: null,
  proposals: [],
  clarification: null,
  sending: false,
  working: false,
  error: null,
};

const message = (error: unknown) => (error instanceof Error ? error.message : 'Неизвестная ошибка');

export const useProposalsStore = create<ProposalsState>()((set, get) => {
  /** Датасет берётся из общего стора; при смене датасета список предложений сбрасывается. */
  function currentDataset(): string | null {
    const datasetId = useAppStore.getState().datasetId;
    if (datasetId !== get().datasetId) set({ ...initialProposalsData, datasetId });
    return datasetId;
  }

  /**
   * План из ответа ставим, только если диспетчер не переключился на другой файл, пока ждали ответ.
   * Применённое предложение переводит план на время события: идущие часы останавливаются без фиксации,
   * иначе следующая фиксация вернула бы план назад, и часы встают на время плана.
   */
  function applyState(datasetId: string, state: PlanningState) {
    const app = useAppStore.getState();
    if (app.datasetId !== datasetId) return;
    app.stopPlayback();
    app.setPlanningState(state);
    if (state.cursor) app.setClock(state.cursor);
  }

  async function mutate(run: (datasetId: string) => Promise<void>) {
    const datasetId = currentDataset();
    if (!datasetId) return;
    set({ working: true, error: null });
    try {
      await run(datasetId);
    } catch (error) {
      set({ error: message(error) });
      if (error instanceof ApiError && error.status === 409) {
        set({ proposals: await getProposals(datasetId).catch(() => get().proposals) });
      }
    } finally {
      set({ working: false });
    }
  }

  return {
    ...initialProposalsData,

    async load(datasetId) {
      if (datasetId !== get().datasetId) set({ ...initialProposalsData, datasetId });
      try {
        const proposals = await getProposals(datasetId);
        if (get().datasetId === datasetId) set({ proposals });
      } catch (error) {
        set({ error: message(error) });
      }
    },

    async send(text) {
      const datasetId = currentDataset();
      if (!datasetId) return false;
      set({ sending: true, error: null, clarification: null });
      try {
        const response = await sendChat(datasetId, text);
        set({
          proposals: response.proposals.reduce(upsertProposal, get().proposals),
          clarification: response.clarification,
        });
        return true;
      } catch (error) {
        set({ error: message(error) });
        return false;
      } finally {
        set({ sending: false });
      }
    },

    approve: (proposalId) =>
      mutate(async (datasetId) => {
        const response = await approveProposal(datasetId, proposalId);
        set({ proposals: upsertProposal(get().proposals, response.proposal) });
        applyState(datasetId, response.state);
      }),

    reject: (proposalId) =>
      mutate(async (datasetId) => {
        set({ proposals: upsertProposal(get().proposals, await rejectProposal(datasetId, proposalId)) });
      }),

    approveAll: () =>
      mutate(async (datasetId) => {
        const response = await approveAllProposals(datasetId);
        set({ proposals: response.proposals });
        applyState(datasetId, response.state);
      }),

    rejectAll: () =>
      mutate(async (datasetId) => {
        set({ proposals: await rejectAllProposals(datasetId) });
      }),
  };
});
