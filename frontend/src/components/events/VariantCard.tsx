import type { ReactNode } from 'react';
import type { Engineer, EventVariant, Metrics, VariantOption } from '../../api/types';
import { brigadeName, formatKm, formatSigned } from '../../lib/format';

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

/** Кто берёт заявку события в этом варианте. */
function holderText(option: VariantOption, engineers: ReadonlyMap<string, Engineer>): string {
  const id = option.request_engineer_id;
  if (!id) return 'заявка остаётся без бригады';
  return `заявку берёт ${brigadeName(engineers.get(id)?.name ?? id)}`;
}

interface CardProps {
  option: VariantOption;
  before: Metrics;
  lateBefore: number;
  current: EventVariant | null;
  locked: boolean;
  /** Бригады дня: карточка называет ту, что берёт заявку. */
  engineers: ReadonlyMap<string, Engineer>;
  /** Событие про одну заявку: карточка говорит, кто её берёт. */
  holder: boolean;
  /** Заголовок над плюсами и минусами и подпись, когда отличий нет: у карточки «отдать бригаде». */
  compare?: { head: string; same: string };
  /** Под кнопкой «Выбрать»: у карточки «отдать бригаде» — список бригад. */
  children?: ReactNode;
  onChoose(variant: EventVariant): void;
}

/** Карточка варианта исправления: итоги плана, плюсы и минусы, кнопка выбора. */
export function VariantCard({ option, before, lateBefore, current, locked, engineers, holder, compare, children, onChoose }: CardProps) {
  const chosen = current === option.variant;
  const same = option.pros.length === 0 && option.cons.length === 0;
  return (
    <article className={`variant${option.recommended ? ' variant--recommended' : ''}`} aria-label={option.title}>
      <header className="variant__head">
        {option.recommended && <span className="badge badge--diff">Рекомендуем</span>}
        <h3>{option.title}</h3>
        <p className="muted">{option.summary}</p>
      </header>
      {holder && <p className="variant__holder">{holderText(option, engineers)}</p>}
      <dl className="variant__numbers">
        <VariantNumber label="Без инженера" value={option.metrics.unassigned} before={before.unassigned} />
        <VariantNumber label="Опоздания" value={option.late} before={lateBefore} />
        <VariantNumber label="Бригад" value={option.metrics.engineers_used} before={before.engineers_used} />
        <VariantNumber label="Переносов" value={option.moved} />
        <VariantNumber label="Пробег" value={option.metrics.total_km} before={before.total_km} km />
      </dl>
      {compare && <p className="variant__compare">{compare.head}</p>}
      {compare && same && <p className="muted variant__same">{compare.same}</p>}
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
      {children}
    </article>
  );
}
