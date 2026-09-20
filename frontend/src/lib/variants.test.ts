import { describe, expect, it } from 'vitest';
import type { BaseVariant, EventType } from '../api/types';
import { makeEventChoice, makePlanningState, makeReassignEvent, makeTimelineItem, makeVariantOption } from '../test/fixtures';
import { reassignEvent } from './events';
import {
  ALL_SAME_TEXT,
  assignedEngineer,
  assignVariant,
  CHOOSABLE_EVENTS,
  choiceEvent,
  isAssignVariant,
  sameAsText,
  VARIANT_TITLES,
  variantTitle,
} from './variants';

describe('variants', () => {
  it('offers variants for a reassignment like for the other breaking events', () => {
    expect([...CHOOSABLE_EVENTS].sort()).toEqual(
      ['engineer_delayed', 'engineer_transport_changed', 'engineer_unavailable', 'request_reassigned', 'urgent'].sort(),
    );
    expect(CHOOSABLE_EVENTS.has('cancel')).toBe(false);
  });

  it('names «ничего не менять» of a reassignment as the insertion into the route, like the server', () => {
    expect(variantTitle('keep', 'request_reassigned')).toBe('Вставить в маршрут');
    expect(variantTitle('optimal', 'request_reassigned')).toBe('Оптимально по дню');
    expect(variantTitle('stable', 'request_reassigned')).toBe('Минимум перестановок');
    const others: EventType[] = ['urgent', 'engineer_unavailable', 'engineer_transport_changed', 'engineer_delayed'];
    for (const type of others) {
      for (const variant of Object.keys(VARIANT_TITLES) as BaseVariant[]) {
        expect(variantTitle(variant, type)).toBe(VARIANT_TITLES[variant]);
      }
    }
    expect(variantTitle('keep', 'engineer_delayed')).toBe('Ничего не менять');
  });

  it('names the variant «отдать заявку бригаде» by the brigade of the day', () => {
    expect(assignVariant('E02')).toBe('assign:E02');
    expect(assignedEngineer('assign:E02')).toBe('E02');
    expect(assignedEngineer('keep')).toBeNull();
    expect(isAssignVariant('assign:E02')).toBe(true);
    expect(isAssignVariant('optimal')).toBe(false);
    expect(variantTitle('assign:E02', 'urgent', makePlanningState().engineers)).toBe('Отдать: Бригада Белузин');
    // Бригады дня нет под рукой: остаётся номер.
    expect(variantTitle('assign:E09', 'urgent')).toBe('Отдать: Бригада E09');
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
      const state = makePlanningState({ timeline: [makeTimelineItem({ id: 'tl_7', event: planned, status: 'awaiting', choosable: true })] });
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
