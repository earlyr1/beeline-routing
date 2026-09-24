import type { Engineer, EventChoice, EventVariant, VariantOption } from '../../api/types';
import { brigadeOptions, eventRequestId } from '../../lib/events';
import { brigadeName } from '../../lib/format';
import { assignedEngineer, choiceVariantTitle } from '../../lib/variants';
import { useAppStore } from '../../store/useAppStore';
import { BrigadeListbox } from '../BrigadeListbox';
import { VariantCard } from './VariantCard';

interface AssignCardProps {
  choice: EventChoice;
  /** Посчитанный вариант «отдать бригаде»; undefined — бригаду ещё не выбирали. */
  option: VariantOption | undefined;
  engineers: ReadonlyMap<string, Engineer>;
  holder: boolean;
  locked: boolean;
  onChoose(variant: EventVariant): void;
  /** Список бригад открылся или закрылся: пока он открыт, Esc закрывает его, а не окно. */
  onListOpenChange(open: boolean): void;
}

/**
 * Четвёртая карточка у события об одной заявке (срочная, изменение, переназначение, возврат): диспетчер не согласен
 * с оптимизатором и отдаёт заявку выбранной бригаде. План считается только после выбора бригады, а цена решения
 * показана против «Оптимально по дню».
 */
export function AssignCard({ choice, option, engineers, holder, locked, onChoose, onListOpenChange }: AssignCardProps) {
  const state = useAppStore((s) => s.state);
  const loading = useAppStore((s) => s.assignLoading);
  const previewAssign = useAppStore((s) => s.previewAssign);

  const requestId = eventRequestId(choice.event);
  // Заявка такой, какой её сделало событие: у изменения, которое ждёт выбора, в плане дня она ещё прежняя,
  // а навык и транспорт для списка бригад нужны новые. У переназначения и возврата заявки в событии нет.
  const request = choice.event.request ?? state?.requests.find((item) => item.id === requestId) ?? null;
  // «В плане» отмечена бригада, которая берёт заявку в оптимальном плане: с ним диспетчер и спорит.
  const optimal = choice.variants.find((item) => item.variant === 'optimal');
  const options = state && request ? brigadeOptions(request, state.engineers, state.plan, optimal?.request_engineer_id) : [];
  // Выбор диспетчера, сделанный раньше: варианты, открытые заново, приходят без посчитанного плана.
  const chosenId = choice.current ? assignedEngineer(choice.current) : null;

  const picker = (
    <BrigadeListbox
      label="Кому отдать заявку"
      text={option ? 'Выбрать другую бригаду' : 'Выбрать бригаду'}
      options={options}
      disabled={locked || loading || options.length === 0}
      // Посчитанная карточка заменяет собой карточку с кнопкой: фокус остаётся на выборе бригады.
      autoFocus={option !== undefined}
      onChoose={(item) => void previewAssign(item.engineer.id)}
      onOpenChange={onListOpenChange}
    />
  );
  const waiting = loading && <p className="muted variant__waiting">Считаем план…</p>;

  if (!option) {
    return (
      <article className="variant variant--assign" aria-label="Другая бригада…">
        <header className="variant__head">
          <h3>Другая бригада…</h3>
          <p className="muted">Посчитаем план, если заявку возьмёт выбранная бригада</p>
        </header>
        {chosenId && <p className="variant__holder">{`Сейчас выбрано: ${brigadeName(engineers.get(chosenId)?.name ?? chosenId)}`}</p>}
        <div className="variant__picker">
          {picker}
          {waiting}
        </div>
      </article>
    );
  }

  const base = choiceVariantTitle(choice, option.compared_to ?? 'optimal', state?.engineers);
  return (
    <VariantCard
      option={option}
      before={choice.metrics_before}
      lateBefore={choice.late_before}
      current={choice.current}
      locked={locked || loading}
      engineers={engineers}
      holder={holder}
      compareHead={`Цена решения против «${base}»`}
      sameText={`То же, что «${base}»`}
      onChoose={onChoose}
    >
      <div className="variant__picker">
        {picker}
        {waiting}
      </div>
    </VariantCard>
  );
}
