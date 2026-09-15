import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { initialProposalsData, useProposalsStore } from '../../store/useProposalsStore';
import { makePlanningState } from '../../test/fixtures';
import { makeProposal, makeUrgentProposal } from '../../test/proposalFixtures';
import { resetStore } from '../../test/store';
import { ProposalsTab } from './ProposalsTab';

const pristine = useProposalsStore.getState();
const enabled = { yandex_maps_api_key: null, llm_enabled: true, osrm_available: true };

function setup(patch = {}) {
  const actions = {
    load: vi.fn().mockResolvedValue(undefined),
    send: vi.fn().mockResolvedValue(true),
    approve: vi.fn().mockResolvedValue(undefined),
    reject: vi.fn().mockResolvedValue(undefined),
    approveAll: vi.fn().mockResolvedValue(undefined),
    rejectAll: vi.fn().mockResolvedValue(undefined),
  };
  useProposalsStore.setState({ ...pristine, ...initialProposalsData, datasetId: 'd_test', ...actions, ...patch }, true);
  return actions;
}

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState(), config: enabled });
});

describe('ProposalsTab', () => {
  it('explains how to enable the assistant when LLM is not configured', () => {
    setup();
    useAppStore.setState({ config: { ...enabled, llm_enabled: false } });
    render(<ProposalsTab />);
    expect(screen.getByText(/Помощник не настроен/)).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('sends the message and clears the field on success', async () => {
    const actions = setup();
    render(<ProposalsTab />);
    expect(actions.load).toHaveBeenCalledWith('d_test');
    const field = screen.getByLabelText('Сообщение помощнику');
    fireEvent.change(field, { target: { value: '  Арташкин заболел после обеда ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(actions.send).toHaveBeenCalledWith('Арташкин заболел после обеда'));
    await waitFor(() => expect(field).toHaveValue(''));
  });

  it('renders cards newest first with approve and reject for pending ones', () => {
    const actions = setup({
      proposals: [
        makeProposal({ status: 'approved', result_diff: makePlanningState().last_diff }),
        makeUrgentProposal(),
        makeProposal({ id: 'pr_3', status: 'failed', error: 'Заявка 50104 уже отменена.' }),
      ],
      clarification: 'Уточните, какую заявку вернуть?',
    });
    render(<ProposalsTab />);
    expect(screen.getByRole('status')).toHaveTextContent('Уточните, какую заявку вернуть?');

    const cards = screen.getAllByRole('listitem').filter((item) => item.classList.contains('proposal'));
    expect(cards.map((card) => card.querySelector('strong')?.textContent)).toEqual([
      'Отмена заявки 50104 в 13:30',
      'Срочная заявка URG-AI-001 в 13:30',
      'Отмена заявки 50104 в 13:30',
    ]);
    expect(within(cards[0]).getByText('Не применилось')).toBeInTheDocument();
    expect(within(cards[0]).getByText('Заявка 50104 уже отменена.')).toBeInTheDocument();
    expect(within(cards[2]).getByText(/^Новых назначений: 1/)).toBeInTheDocument();
    expect(within(cards[2]).queryByRole('button', { name: 'Применить' })).not.toBeInTheDocument();

    fireEvent.click(within(cards[1]).getByRole('button', { name: 'Применить' }));
    expect(actions.approve).toHaveBeenCalledWith('pr_2');
    fireEvent.click(within(cards[1]).getByRole('button', { name: 'Отклонить' }));
    expect(actions.reject).toHaveBeenCalledWith('pr_2');

    fireEvent.click(screen.getByRole('button', { name: 'Применить все (1)' }));
    expect(actions.approveAll).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Отклонить все' }));
    expect(actions.rejectAll).toHaveBeenCalled();
  });

  it('locks approving while the plan is being moved to the clock', () => {
    setup({ proposals: [makeProposal()] });
    useAppStore.setState({ committing: true });
    render(<ProposalsTab />);
    expect(screen.getByRole('button', { name: 'Применить' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Применить все (1)' })).toBeDisabled();
  });

  it('locks actions while the previous plan is shown', () => {
    setup({ proposals: [makeProposal()] });
    useAppStore.setState({ showPrevious: true });
    render(<ProposalsTab />);
    expect(screen.getByRole('button', { name: 'Применить' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Применить все (1)' })).toBeDisabled();
    expect(screen.getByText(/Переключитесь на план «После события»/)).toBeInTheDocument();
  });
});
