import { useEffect } from 'react';
import type { EventVariant, Metrics, VariantOption } from '../../api/types';
import { describeEvent } from '../../lib/events';
import { formatKm, formatSigned } from '../../lib/format';
import { byId } from '../../lib/planView';
import { choiceEvent } from '../../lib/variants';
import { useAppStore } from '../../store/useAppStore';

interface NumberProps {
  label: string;
  value: number;
  before?: number;
  km?: boolean;
}

/** Крупная цифра варианта и сдвиг к плану до события. */
function VariantNumber({ label, value, before, km = false }: NumberProps) {
  const delta = before === undefined ? null : formatSigned(value - before, km ? 1 : 0);
  return (
    <div className="variant__number">
      <dt>{label}</dt>
      <dd>
        <strong>{km ? formatKm(value) : value}</strong>
        {delta !== null && delta !== '0' && <span className="variant__delta">{delta}</span>}
      </dd>
    </div>
  );
}

interface CardProps {
  option: VariantOption;
  before: Metrics;
  lateBefore: number;
  current: EventVariant | null;
  locked: boolean;
  onChoose(variant: EventVariant): void;
}

function VariantCard({ option, before, lateBefore, current, locked, onChoose }: CardProps) {
  const chosen = current === option.variant;
  return (
    <article className={`variant${option.recommended ? ' variant--recommended' : ''}`} aria-label={option.title}>
      <header className="variant__head">
        {option.recommended && <span className="badge badge--diff">Рекомендуем</span>}
        <h3>{option.title}</h3>
        <p className="muted">{option.summary}</p>
      </header>
      <dl className="variant__numbers">
        <VariantNumber label="Без инженера" value={option.metrics.unassigned} before={before.unassigned} />
        <VariantNumber label="Опоздания" value={option.late} before={lateBefore} />
        <VariantNumber label="Бригад" value={option.metrics.engineers_used} before={before.engineers_used} />
        <VariantNumber label="Переносов" value={option.moved} />
        <VariantNumber label="Пробег" value={option.metrics.total_km} before={before.total_km} km />
      </dl>
      {option.pros.length > 0 && (
        <ul className="variant__pros">
          {option.pros.map((text) => (
            <li key={text}>{`✓ ${text}`}</li>
          ))}
        </ul>
      )}
      {option.cons.length > 0 && (
        <ul className="variant__cons">
          {option.cons.map((text) => (
            <li key={text}>{`✗ ${text}`}</li>
          ))}
        </ul>
      )}
      <button
        type="button"
        className={`btn${option.recommended ? ' btn-primary' : ''} variant__choose`}
        // Рекомендованный вариант выбирается Enter сразу после открытия окна.
        autoFocus={option.recommended}
        disabled={locked || chosen}
        onClick={() => onChoose(option.variant)}
      >
        {chosen ? 'Выбрано' : 'Выбрать'}
      </button>
    </article>
  );
}

/**
 * Окно выбора варианта исправления при «ломающем» событии: три карточки, рекомендованная выделена.
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

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
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
  // У переназначения заголовок называет и бригаду, от которой уходит заявка.
  const title = choice ? describeEvent(state ? choiceEvent(choice, state) : choice.event, engineers, requests) : 'Событие дня';

  return (
    <div className="choice-overlay">
      <section className="choice" role="dialog" aria-modal="true" aria-labelledby="choice-title">
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
            {choice.variants.map((option) => (
              <VariantCard
                key={option.variant}
                option={option}
                before={choice.metrics_before}
                lateBefore={choice.late_before}
                current={choice.current}
                locked={busy}
                onChoose={(variant) => void chooseVariant(variant)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
