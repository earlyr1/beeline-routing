import { useCallback, useEffect, useRef } from 'react';
import { describeEvent } from '../../lib/events';
import { byId } from '../../lib/planView';
import { choiceEvent, choiceHasHolder, isAssignVariant, sameAsText } from '../../lib/variants';
import { useAppStore } from '../../store/useAppStore';
import { AssignCard } from './AssignCard';
import { VariantCard } from './VariantCard';

/**
 * Окно выбора варианта исправления: три посчитанные карточки, рекомендованная выделена, а у события об одной заявке
 * четвёртая — «отдать заявку выбранной бригаде». Само открывается у события любого типа, когда «Ничего не менять»
 * ломает план больше лучшего пересчёта; у остальных событий его открывает «Варианты…» на шкале.
 * Часы стоят, пока окно открыто; закрытое без выбора окно оставляет часы на событии.
 */
export function ChoiceDialog() {
  const state = useAppStore((s) => s.state);
  const choice = useAppStore((s) => s.choice);
  const loading = useAppStore((s) => s.choiceLoading);
  const busy = useAppStore((s) => s.busy);
  const chooseVariant = useAppStore((s) => s.chooseVariant);
  const closeChoice = useAppStore((s) => s.closeChoice);
  const open = loading || choice !== null;
  // Список бригад лежит выше окна: пока он открыт, Esc закрывает только его.
  const listOpen = useRef(false);
  const onListOpenChange = useCallback((value: boolean) => {
    listOpen.current = value;
  }, []);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || listOpen.current) return;
      // Окно выбора — верхний слой: Esc закрывает только его.
      event.preventDefault();
      closeChoice();
    };
    document.addEventListener('keydown', closeOnEscape, true);
    return () => document.removeEventListener('keydown', closeOnEscape, true);
  }, [open, closeChoice]);

  if (!open) return null;
  const engineers = byId(state?.engineers ?? []);
  const requests = byId(state?.requests ?? []);
  const event = choice ? (state ? choiceEvent(choice, state) : choice.event) : null;
  // У переназначения заголовок называет и бригаду, от которой уходит заявка.
  const title = event ? describeEvent(event, engineers, requests) : 'Событие дня';
  // Событие про одну заявку: каждая карточка называет бригаду, которая её берёт.
  const holder = choice !== null && choiceHasHolder(choice);

  return (
    <div className="choice-overlay">
      <section
        className={`choice${choice?.assignable ? ' choice--wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="choice-title"
      >
        <header className="choice__head">
          <div>
            <p className="choice__eyebrow">Как исправить план</p>
            <h2 id="choice-title">{title}</h2>
          </div>
          <button type="button" className="btn btn-ghost btn-small" aria-label="Закрыть выбор" onClick={closeChoice}>
            ✕
          </button>
        </header>
        {loading && <p className="muted">Считаем варианты…</p>}
        {choice && (
          <div className="choice__cards">
            {choice.variants
              .filter((option) => !isAssignVariant(option.variant))
              .map((option) => (
                <VariantCard
                  key={option.variant}
                  option={option}
                  before={choice.metrics_before}
                  lateBefore={choice.late_before}
                  current={choice.current}
                  locked={busy}
                  engineers={engineers}
                  holder={holder}
                  sameText={sameAsText(choice, option, state?.engineers)}
                  onChoose={(variant) => void chooseVariant(variant)}
                />
              ))}
            {choice.assignable && (
              <AssignCard
                choice={choice}
                option={choice.variants.find((option) => isAssignVariant(option.variant))}
                engineers={engineers}
                holder={holder}
                locked={busy}
                onChoose={(variant) => void chooseVariant(variant)}
                onListOpenChange={onListOpenChange}
              />
            )}
          </div>
        )}
      </section>
    </div>
  );
}
