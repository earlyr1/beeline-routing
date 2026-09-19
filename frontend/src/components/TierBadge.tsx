import type { RequestTier } from '../api/types';
import { TIER_LABELS } from '../lib/format';

/** Подсказка к метке: чем задан приоритет распределения. Ответ организаторов на вопрос 15. */
export const TIER_TITLE = 'Приоритет распределения: авария → подключение → ремонт и дозаказ';

/**
 * Метка уровня распределения заявки. Показывается только у среднего уровня — «Подключение»:
 * авария и так видна меткой «Срочная» и приставкой URG-, а нижний уровень — это все остальные заявки,
 * и метка у каждой второй строки списка только мешала бы.
 */
export function TierBadge({ tier }: { tier: RequestTier | undefined }) {
  if (tier !== 'connection') return null;
  return (
    <span className="badge" title={TIER_TITLE}>
      {TIER_LABELS.connection}
    </span>
  );
}
