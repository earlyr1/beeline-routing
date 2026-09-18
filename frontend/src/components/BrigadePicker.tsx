import type { ServiceRequest } from '../api/types';
import { brigadeOptions, reassignEvent, reassignState } from '../lib/events';
import { brigadeName } from '../lib/format';
import { assignmentIndex, byId } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';
import { BrigadeListbox } from './BrigadeListbox';

/**
 * Выбор бригады в карточке заявки: список всех бригад, бригада текущего плана выделена, неподходящие недоступны
 * с причиной. Выбор другой бригады ставит на время часов событие «Переназначение заявки», а сервер предлагает варианты.
 */
export function BrigadePicker({ request }: { request: ServiceRequest }) {
  const state = useAppStore((s) => s.state);
  const busy = useAppStore((s) => s.busy);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const clock = useAppStore((s) => s.clock);
  const applyEvent = useAppStore((s) => s.applyEvent);
  // Окно выбора варианта открывается и без расчёта, например на событии при проигрывании часов.
  const choosing = useAppStore((s) => s.choice !== null || s.choiceLoading);

  if (!state) return null;

  // Начатую работу проверяем по текущему плану, как у кнопок заявки: события меняют именно его.
  const visit = assignmentIndex(state.plan).get(request.id)?.visit;
  const lock = reassignState(request, visit, { busy, showPrevious, clock });
  // Список лежит выше окна выбора варианта, а новое событие сбросило бы выбор: пока окно открыто, бригаду не выбирают.
  const locked = lock.disabled || choosing;
  const options = brigadeOptions(request, state.engineers, state.plan);

  const engineers = byId(state.engineers);
  const current = options.find((option) => option.current)?.engineer;
  const fixed = request.fixed_engineer_id ? engineers.get(request.fixed_engineer_id) : undefined;
  // Выбор диспетчера держится и после событий; если бригада в плане уже другая, называем выбранную.
  const fixedNote = !request.fixed_engineer_id
    ? null
    : request.fixed_engineer_id === current?.id
      ? 'выбрана диспетчером'
      : `диспетчер выбрал: ${brigadeName(fixed?.name ?? request.fixed_engineer_id)}`;

  return (
    <BrigadeListbox
      label="Бригада:"
      labelVisible
      text={current ? brigadeName(current.name) : 'Без бригады'}
      dot
      dotEngineerId={current?.id ?? null}
      options={options}
      disabled={locked}
      title={lock.title}
      onChoose={(option) => {
        // Бригада заявки уже выбрана: её выбор просто закрывает список.
        if (!option.current) void applyEvent(reassignEvent(request.id, option.engineer.id, clock));
      }}
    >
      {fixedNote && <span className="muted brigade-picker__fixed">{fixedNote}</span>}
    </BrigadeListbox>
  );
}
