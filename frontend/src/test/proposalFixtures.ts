import type { Proposal } from '../api/types';
import { makePlanningState } from './fixtures';

export function makeProposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: 'pr_1',
    status: 'pending',
    event: { type: 'cancel', time: '13:30', request: null, request_id: '50104', engineer_id: null },
    rationale: 'В сообщении сказано, что клиент на Грайвороновской отказался от визита.',
    source_text: 'Клиент на Грайвороновской отказался',
    created_at_version: 4,
    result_diff: null,
    error: null,
    ...overrides,
  };
}

export function makeUrgentProposal(overrides: Partial<Proposal> = {}): Proposal {
  const request = { ...makePlanningState().requests[7], id: 'URG-AI-001', address: 'Город Москва, ул.Таганская, д. 3' };
  return makeProposal({
    id: 'pr_2',
    event: { type: 'urgent', time: '13:30', request, request_id: null, engineer_id: null },
    rationale: 'Диспетчер сообщил об аварии на Таганской.',
    source_text: 'Авария на Таганской, 3',
    ...overrides,
  });
}
