import { describe, expect, it } from 'vitest';
import type { BaseVariant } from '../api/types';
import {
  makeEventChoice,
  makePlanningState,
  makeReassignEvent,
  makeRequestUpdateEvent,
  makeTimelineItem,
  makeUrgentChoice,
  makeVariantOption,
} from '../test/fixtures';
import { cancelEvent, reassignEvent } from './events';
import {
  ALL_SAME_TEXT,
  assignedEngineer,
  assignVariant,
  choiceEvent,
  choiceHasHolder,
  isAssignVariant,
  sameAsText,
  timelineVariantText,
  VARIANT_TITLES,
  variantTitle,
} from './variants';

describe('variants', () => {
  it('names the three strategies the same for every event, like the server', () => {
    // Особых названий по типу события больше нет: «Вставить в маршрут» у переназначения стало «Ничего не менять».
    expect(Object.keys(VARIANT_TITLES).map((variant) => variantTitle(variant as BaseVariant))).toEqual([
      'Оптимально по дню',
      'Минимум перестановок',
      'Ничего не менять',
    ]);
  });

  it('names the variant «отдать заявку бригаде» by the brigade of the day', () => {
    expect(assignVariant('E02')).toBe('assign:E02');
    expect(assignedEngineer('assign:E02')).toBe('E02');
    expect(assignedEngineer('keep')).toBeNull();
    expect(isAssignVariant('assign:E02')).toBe(true);
    expect(isAssignVariant('optimal')).toBe(false);
    expect(variantTitle('assign:E02', makePlanningState().engineers)).toBe('Отдать: Бригада Белузин');
    // Бригады дня нет под рукой: остаётся номер.
    expect(variantTitle('assign:E09')).toBe('Отдать: Бригада E09');
  });

  describe('timelineVariantText', () => {
    it('says that «ничего не менять» was taken without a choice window because there was nothing to choose from', () => {
      const item = makeTimelineItem({ event: makeRequestUpdateEvent(), variant: 'keep', variant_auto: true });
      expect(timelineVariantText(item)).toBe('Вариант: Ничего не менять — выбирать было не из чего');
      // Посчитанное впереди событие проходит план так же.
      expect(timelineVariantText({ ...item, status: 'pending' })).toBe('Вариант: Ничего не менять — выбирать было не из чего');
    });

    it('names the choice of the dispatcher as it is', () => {
      expect(timelineVariantText(makeTimelineItem({ variant: 'keep' }))).toBe('Вариант: Ничего не менять');
      expect(timelineVariantText(makeTimelineItem({ event: makeReassignEvent(), variant: 'keep' }))).toBe('Вариант: Ничего не менять');
      expect(timelineVariantText(makeTimelineItem({ variant: 'assign:E02' }), makePlanningState().engineers)).toBe('Вариант: Отдать: Бригада Белузин');
    });

    it('has nothing to say while the server has not decided yet or the event waits for a choice', () => {
      expect(timelineVariantText(makeTimelineItem({ status: 'pending', variant: null }))).toBeNull();
      expect(timelineVariantText(makeTimelineItem({ status: 'awaiting', variant: null }))).toBeNull();
    });
  });

  describe('choiceHasHolder', () => {
    const nobody = (choice = makeEventChoice()) => choice.variants.map((option) => ({ ...option, request_engineer_id: null }));

    it('names the brigade of the request for any event about one request, whatever its type', () => {
      expect(choiceHasHolder(makeUrgentChoice())).toBe(true);
      // Изменение заявки: в плане ни одного варианта её никто не берёт, но отдать её бригаде можно.
      const edit = makeEventChoice({ event: makeRequestUpdateEvent(), assignable: true, variants: nobody() });
      expect(choiceHasHolder(edit)).toBe(true);
      // Бригаду сервер назвал хоть в одном варианте.
      const named = makeEventChoice({ variants: makeEventChoice().variants.map((option, k) => ({ ...option, request_engineer_id: k === 0 ? 'E02' : null })) });
      expect(choiceHasHolder(named)).toBe(true);
    });

    it('names nobody for events about an engineer and for a cancellation', () => {
      expect(choiceHasHolder(makeEventChoice())).toBe(false);
      expect(choiceHasHolder(makeEventChoice({ event: cancelEvent('46393', '13:00'), assignable: false, variants: nobody() }))).toBe(false);
    });
  });

  describe('sameAsText', () => {
    /** Варианты, у которых «Минимум перестановок» повторяет рекомендованный, а «Ничего не менять» отличается. */
    const tied = () =>
      makeEventChoice({
        variants: [
          makeVariantOption('optimal', { recommended: true, compared_to: 'keep', pros: ['на 1 клиента без инженера или с опозданием меньше'] }),
          makeVariantOption('stable', { compared_to: 'optimal' }),
          makeVariantOption('keep', { compared_to: 'optimal', cons: ['на 1 клиента без инженера или с опозданием больше'] }),
        ],
      });

    it('names the variant a card repeats instead of leaving both lists empty', () => {
      const choice = tied();
      expect(sameAsText(choice, choice.variants[1])).toBe('То же, что «Оптимально по дню»');
      // Подпись готовится по compared_to, а показывает её карточка — только когда отличий нет.
      expect(sameAsText(choice, choice.variants[0])).toBe('То же, что «Ничего не менять»');
    });

    it('says that every variant is the same when the recommended one has nothing to compare itself with', () => {
      const choice = makeEventChoice({
        variants: [
          makeVariantOption('optimal', { recommended: true, compared_to: null }),
          makeVariantOption('stable', { compared_to: 'optimal' }),
          makeVariantOption('keep', { compared_to: 'optimal' }),
        ],
      });
      expect(sameAsText(choice, choice.variants[0])).toBe(ALL_SAME_TEXT);
      expect(sameAsText(choice, choice.variants[2])).toBe('То же, что «Оптимально по дню»');
    });

    it('says nothing when the window has a single variant', () => {
      const choice = makeEventChoice({ variants: [makeVariantOption('optimal', { recommended: true, compared_to: null })] });
      expect(sameAsText(choice, choice.variants[0])).toBeUndefined();
    });
  });

  describe('choiceEvent', () => {
    // В окне выбора сервер присылает событие без прежней бригады, как его запланировали.
    const planned = reassignEvent('50104', 'E02', '13:30');
    const choiceOf = (event = planned) => makeEventChoice({ entry_id: 'tl_7', event });

    it('takes the previous brigade of a reassignment waiting for a choice from the current plan', () => {
      const state = makePlanningState({ timeline: [makeTimelineItem({ id: 'tl_7', event: planned, status: 'awaiting' })] });
      expect(choiceEvent(choiceOf(), state)).toEqual({ ...planned, previous_engineer_id: 'E01' });
      // Событие впереди: план тоже ещё до него.
      expect(choiceEvent(choiceOf(), makePlanningState())).toEqual({ ...planned, previous_engineer_id: 'E01' });
    });

    it('leaves the previous brigade empty for a request without a brigade or already with the chosen one', () => {
      const unassigned = reassignEvent('18754', 'E01', '13:30');
      expect(choiceEvent(choiceOf(unassigned), makePlanningState())).toBe(unassigned);
      const same = reassignEvent('50104', 'E01', '13:30');
      expect(choiceEvent(choiceOf(same), makePlanningState())).toBe(same);
    });

    it('takes the previous brigade of an applied reassignment recorded by the server, not the plan after it', () => {
      const applied = (previous: string | null) =>
        makePlanningState({ timeline: [makeTimelineItem({ id: 'tl_7', event: makeReassignEvent({ previous_engineer_id: previous }), status: 'applied' })] });
      expect(choiceEvent(choiceOf(), applied('E03'))).toEqual({ ...planned, previous_engineer_id: 'E03' });
      // Заявка была без бригады: бригаду из плана после события прежней не называем.
      expect(choiceEvent(choiceOf(), applied(null))).toEqual({ ...planned, previous_engineer_id: null });
    });

    it('keeps other events and a previous brigade already in the event', () => {
      const choice = makeEventChoice();
      expect(choiceEvent(choice, makePlanningState())).toBe(choice.event);
      const recorded = makeReassignEvent({ previous_engineer_id: 'E03' });
      expect(choiceEvent(choiceOf(recorded), makePlanningState())).toBe(recorded);
    });
  });
});
