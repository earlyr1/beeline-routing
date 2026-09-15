import { describe, expect, it } from 'vitest';
import { makePlanningState, makeTransportChangeEvent } from '../test/fixtures';
import { makeProposal, makeUrgentProposal } from '../test/proposalFixtures';
import { formatKm } from './format';
import { diffSummary, plural, proposalDetails, upsertProposal } from './proposals';

describe('proposals view helpers', () => {
  const state = makePlanningState();

  it('describes a cancel with the address and the current assignment', () => {
    expect(proposalDetails(makeProposal(), state)).toEqual([
      'ул.Грайвороновская, д. 10 к 2 · окно 14:00–16:00',
      'Сейчас в маршруте: Бригада Арташкин, начало 14:00',
    ]);
  });

  it('counts the visits an unavailable engineer would lose', () => {
    const proposal = makeProposal({ event: { type: 'engineer_unavailable', time: '13:30', request: null, request_id: null, engineer_id: 'E01' } });
    expect(proposalDetails(proposal, state)).toEqual(['В маршруте после 13:30: 2 заявки, их перераспределит оптимизатор']);
    const late = makeProposal({ event: { type: 'engineer_unavailable', time: '21:00', request: null, request_id: null, engineer_id: 'E01' } });
    expect(proposalDetails(late, state)).toEqual(['У Бригада Арташкин нет заявок после 21:00']);
  });

  it('describes a transport change from the current transport and counts car-only requests on a downgrade', () => {
    const downgrade = makeProposal({ event: makeTransportChangeEvent({ previous_transport: null }) });
    expect(proposalDetails(downgrade, state)).toEqual([
      'Бригада Арташкин: Автомобиль → Велосипед с 13:30',
      'Заявок с требованием «Автомобиль» после 13:30: 1, их перераспределит оптимизатор',
    ]);
    const late = makeProposal({ event: makeTransportChangeEvent({ time: '16:00', previous_transport: null }) });
    expect(proposalDetails(late, state)).toEqual(['Бригада Арташкин: Автомобиль → Велосипед с 16:00']);
    const upgrade = makeProposal({
      event: makeTransportChangeEvent({ engineer_id: 'E03', transport: 'car', previous_transport: null }),
    });
    expect(proposalDetails(upgrade, state)).toEqual(['Бригада Комарь: Пешеход → Автомобиль с 13:30']);
  });

  it('keeps the old transport of an applied transport change after the engineer switched', () => {
    const switched = {
      ...state,
      engineers: state.engineers.map((engineer) => (engineer.id === 'E01' ? { ...engineer, transport: 'bike' as const } : engineer)),
    };
    const approved = makeProposal({ status: 'approved', event: makeTransportChangeEvent({ time: '16:00' }) });
    expect(proposalDetails(approved, switched)).toEqual(['Бригада Арташкин: Автомобиль → Велосипед с 16:00']);
  });

  it('describes an urgent request with window, skill and transport', () => {
    expect(proposalDetails(makeUrgentProposal(), state)).toEqual([
      'ул.Таганская, д. 3 · окно 13:00–15:00, 60 мин',
      'Аварийные работы',
      'Нужен транспорт: Автомобиль',
    ]);
  });

  it('summarises the diff of an applied proposal', () => {
    const diff = state.last_diff!;
    expect(diffSummary(diff)).toBe(
      `Новых назначений: 1 · перенесено: 1 · снято: 0 · инженеров 2 → 2 · пробег ${formatKm(diff.metrics_before.total_km)} → ${formatKm(diff.metrics_after.total_km)}`,
    );
  });

  it('pluralises Russian nouns and upserts proposals by id', () => {
    expect([1, 2, 5, 11, 22].map((n) => plural(n, 'заявка', 'заявки', 'заявок'))).toEqual(['заявка', 'заявки', 'заявок', 'заявок', 'заявки']);
    const first = makeProposal();
    const updated = { ...first, status: 'approved' as const };
    expect(upsertProposal([first], updated)).toEqual([updated]);
    expect(upsertProposal([first], makeUrgentProposal()).map((item) => item.id)).toEqual(['pr_1', 'pr_2']);
  });
});
