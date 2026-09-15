import type { MouseEvent } from 'react';
import type { ServiceRequest } from '../api/types';
import { requestActionState } from '../lib/events';
import { assignmentIndex } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';

/**
 * Кнопки «Изменить» и «Отменить» или «Вернуть» у заявки. Одни и те же в строке списка заявок и в карточке заявки,
 * поэтому правила доступности и время события у них не расходятся: событие ставится на время часов дня.
 */
export function RequestActions({ request }: { request: ServiceRequest }) {
  const state = useAppStore((s) => s.state);
  const busy = useAppStore((s) => s.busy);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const clock = useAppStore((s) => s.clock);
  const applyEvent = useAppStore((s) => s.applyEvent);
  const startEdit = useAppStore((s) => s.startEdit);
  if (!state) return null;

  // Начатую работу проверяем по текущему плану: события меняют его, даже когда на экране план до события.
  const visit = assignmentIndex(state.plan).get(request.id)?.visit;
  const actions = requestActionState(request, visit, { busy, showPrevious, clock });
  // Кнопки стоят и внутри строки списка: клик по ним не выбирает заявку.
  const handle = (action: () => void) => (event: MouseEvent) => {
    event.stopPropagation();
    action();
  };

  return (
    <>
      <button
        type="button"
        className="btn btn-small"
        disabled={actions.disabled}
        title={actions.editTitle}
        onClick={handle(() => startEdit(request.id))}
      >
        Изменить
      </button>
      <button
        type="button"
        className="btn btn-small"
        disabled={actions.disabled}
        title={actions.cancelTitle}
        onClick={handle(() => void applyEvent(actions.cancelEvent))}
      >
        {actions.cancelLabel}
      </button>
    </>
  );
}
