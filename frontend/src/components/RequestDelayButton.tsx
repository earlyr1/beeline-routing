import type { ServiceRequest } from '../api/types';
import { requestDelayState } from '../lib/events';
import { assignmentIndex } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';

/**
 * «Задержка бригады» в карточке заявки: у начатой заявки «Изменить» недоступен, а работа затянулась — задержку
 * ставят прямо отсюда, а не со страницы бригады с ручным временем. Кнопка открывает тот же диалог «Задержка инженера»
 * с бригадой визита и временем «Задержка с»; дальше обычное событие задержки. Есть, только пока визит «В работе»
 * или бригада «В пути» к нему.
 */
export function RequestDelayButton({ request }: { request: ServiceRequest }) {
  const state = useAppStore((s) => s.state);
  const busy = useAppStore((s) => s.busy);
  const clock = useAppStore((s) => s.clock);
  const startDelay = useAppStore((s) => s.startDelay);
  if (!state) return null;

  // Визит и бригада — из текущего плана, как у кнопок «Изменить» и «Отменить».
  const delay = requestDelayState(request, assignmentIndex(state.plan).get(request.id), state.engineers, { busy, clock });
  if (!delay) return null;

  return (
    <button
      type="button"
      className="btn btn-small"
      disabled={delay.disabled}
      title={delay.title}
      onClick={() => startDelay(delay.engineerId, delay.time)}
    >
      Задержка бригады
    </button>
  );
}
