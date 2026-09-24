import type {
  AssignVariant,
  BaseVariant,
  Engineer,
  EventChoice,
  EventVariant,
  TimelineItem,
  VariantOption,
} from '../api/types';
import { brigadeName } from './format';

/** Названия трёх готовых стратегий, как в ответах сервера: у всех событий одни и те же. */
export const VARIANT_TITLES: Record<BaseVariant, string> = {
  optimal: 'Оптимально по дню',
  stable: 'Минимум перестановок',
  keep: 'Ничего не менять',
};

/** Приставка варианта «отдать заявку бригаде». */
const ASSIGN_PREFIX = 'assign:';

/** Токен варианта «отдать заявку этой бригаде». */
export function assignVariant(engineerId: string): AssignVariant {
  return `${ASSIGN_PREFIX}${engineerId}`;
}

/** Вариант «отдать заявку бригаде»: его считают по выбору диспетчера, а не заранее. */
export function isAssignVariant(variant: EventVariant): variant is AssignVariant {
  return variant.startsWith(ASSIGN_PREFIX);
}

/** Бригада из токена assign:<инженер>; null — это одна из трёх готовых стратегий. */
export function assignedEngineer(variant: EventVariant): string | null {
  return isAssignVariant(variant) ? variant.slice(ASSIGN_PREFIX.length) : null;
}

/**
 * Название стратегии, как в ответе сервера. У «отдать бригаде» имя бригады ищется среди бригад дня:
 * там, где под рукой сам вариант, берите его title сервера.
 */
export function variantTitle(variant: EventVariant, engineers: Engineer[] = []): string {
  if (isAssignVariant(variant)) {
    const assigned = assignedEngineer(variant) ?? '';
    return `Отдать: ${brigadeName(engineers.find((item) => item.id === assigned)?.name ?? assigned)}`;
  }
  return VARIANT_TITLES[variant];
}

/** Подпись стратегии, которую выбрал проход шкалы, а не диспетчер. */
export const AUTO_VARIANT_NOTE = 'выбирать было не из чего';

/**
 * Стратегия события на шкале словами: выбор диспетчера или «Ничего не менять», если окно выбора не понадобилось.
 * null — стратегии у события нет: сервер его ещё не посчитал или ждёт выбора.
 */
export function timelineVariantText(item: TimelineItem, engineers: Engineer[] = []): string | null {
  if (!item.variant) return null;
  const title = `Вариант: ${variantTitle(item.variant, engineers)}`;
  return item.variant_auto ? `${title} — ${AUTO_VARIANT_NOTE}` : title;
}

/** Название варианта в окне: у посчитанного берётся заголовок сервера, у остального — собранное по токену. */
export function choiceVariantTitle(choice: EventChoice, variant: EventVariant, engineers: Engineer[] = []): string {
  return choice.variants.find((item) => item.variant === variant)?.title ?? variantTitle(variant, engineers);
}

/**
 * Событие об одной заявке: каждая карточка окна называет бригаду, которая её берёт. Тип события не проверяется:
 * заявку можно отдать бригаде (assignable) или хоть в одном варианте сервер назвал её бригаду.
 */
export function choiceHasHolder(choice: EventChoice): boolean {
  return choice.assignable || choice.variants.some((option) => option.request_engineer_id !== null);
}

/** Подпись рекомендованной карточки, когда у всех вариантов одни и те же числа. */
export const ALL_SAME_TEXT = 'Все варианты одинаковые: выбор ничего не меняет';

/**
 * Что карточка скажет вместо пустых «чем лучше / хуже»: с каким вариантом она совпала или, у рекомендованного,
 * что одинаковы все. undefined — сравнивать не с чем (вариант в окне один). Показывает эту подпись сама карточка,
 * и только когда отличий нет.
 */
export function sameAsText(choice: EventChoice, option: VariantOption, engineers: Engineer[] = []): string | undefined {
  if (option.compared_to) return `То же, что «${choiceVariantTitle(choice, option.compared_to, engineers)}»`;
  return option.recommended && choice.variants.length > 1 ? ALL_SAME_TEXT : undefined;
}
