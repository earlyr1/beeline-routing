import { useEffect, useState } from 'react';
import type { PlanningState, Proposal } from '../../api/types';
import { describeEvent } from '../../lib/events';
import { byId } from '../../lib/planView';
import { diffSummary, PROPOSAL_STATUS_LABELS, proposalDetails } from '../../lib/proposals';
import { useAppStore } from '../../store/useAppStore';
import { useProposalsStore } from '../../store/useProposalsStore';

const PLACEHOLDER = 'Например: Арташкин заболел после обеда, а заявку на Дубининской клиент отменил';

interface CardProps {
  proposal: Proposal;
  state: PlanningState;
  disabled: boolean;
  onApprove(proposalId: string): void;
  onReject(proposalId: string): void;
}

function ProposalCard({ proposal, state, disabled, onApprove, onReject }: CardProps) {
  const details = proposalDetails(proposal, state);
  return (
    <li className={`proposal proposal--${proposal.status}`}>
      <div className="proposal__head">
        <strong>{describeEvent(proposal.event, byId(state.engineers), byId(state.requests))}</strong>
        <span className={`badge proposal__status proposal__status--${proposal.status}`}>
          {PROPOSAL_STATUS_LABELS[proposal.status]}
        </span>
      </div>
      {details.length > 0 && (
        <ul className="proposal__details">
          {details.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
      )}
      <p className="proposal__rationale">{proposal.rationale}</p>
      <p className="muted proposal__source">Из сообщения: «{proposal.source_text}»</p>
      {proposal.error && <p className="error-text">{proposal.error}</p>}
      {proposal.result_diff && <p className="proposal__result">{diffSummary(proposal.result_diff)}</p>}
      {proposal.status === 'pending' && (
        <div className="proposal__actions">
          <button type="button" className="btn btn-small btn-primary" disabled={disabled} onClick={() => onApprove(proposal.id)}>
            Применить
          </button>
          <button type="button" className="btn btn-small" disabled={disabled} onClick={() => onReject(proposal.id)}>
            Отклонить
          </button>
        </div>
      )}
    </li>
  );
}

export function ProposalsTab() {
  const llmEnabled = useAppStore((s) => s.config?.llm_enabled ?? false);
  const datasetId = useAppStore((s) => s.datasetId);
  const state = useAppStore((s) => s.state);
  // Пока план переходит на время часов, применённое предложение пришло бы в план, который сейчас заменят.
  const committing = useAppStore((s) => s.committing);
  const { proposals, clarification, sending, working, error, load, send, approve, reject, approveAll, rejectAll } =
    useProposalsStore();
  const [text, setText] = useState('');

  useEffect(() => {
    if (llmEnabled && datasetId) void load(datasetId);
  }, [llmEnabled, datasetId, load]);

  if (!llmEnabled) {
    return (
      <div className="proposals">
        <p className="note">
          Помощник не настроен. Задайте LLM_BASE_URL, LLM_API_KEY и LLM_MODEL в файле .env и перезапустите backend. Кнопки
          событий на панели работают и без помощника.
        </p>
      </div>
    );
  }
  if (!state) return null;

  const pending = proposals.filter((item) => item.status === 'pending');
  const locked = sending || working || committing;

  return (
    <div className="proposals">
      <form
        className="proposals__chat"
        noValidate
        onSubmit={async (event) => {
          event.preventDefault();
          const message = text.trim();
          // После «Не понял» без предложений текст остаётся в поле: его пишут заново целиком, и проще поправить.
          if (message && (await send(message))) setText('');
        }}
      >
        <label className="field">
          <span>Сообщение помощнику</span>
          <textarea value={text} rows={3} placeholder={PLACEHOLDER} onChange={(event) => setText(event.target.value)} />
        </label>
        <div className="proposals__actions">
          <span className="muted">Помощник только предлагает. План меняется после «Применить».</span>
          <button type="submit" className="btn btn-primary" disabled={locked || !text.trim()}>
            {sending ? 'Разбираю…' : 'Отправить'}
          </button>
        </div>
      </form>
      {clarification && (
        <p className="note proposals__clarification" role="status">
          {clarification}
        </p>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className="tab-toolbar proposals__bulk">
        <strong>Рекомендуемые изменения</strong>
        <button type="button" className="btn btn-small btn-primary" disabled={locked || pending.length === 0} onClick={() => void approveAll()}>
          Применить все ({pending.length})
        </button>
        <button type="button" className="btn btn-small" disabled={locked || pending.length === 0} onClick={() => void rejectAll()}>
          Отклонить все
        </button>
      </div>
      {proposals.length === 0 ? (
        <p className="empty">Пока нет предложений. Опишите ситуацию своими словами.</p>
      ) : (
        <ul className="proposal-list">
          {[...proposals].reverse().map((proposal) => (
            <ProposalCard
              key={proposal.id}
              proposal={proposal}
              state={state}
              disabled={locked}
              onApprove={(id) => void approve(id)}
              onReject={(id) => void reject(id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
