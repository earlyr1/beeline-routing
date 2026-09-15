import { describe, expect, it } from 'vitest';
import { makePlanningState } from '../test/fixtures';
import {
  assignmentIndex,
  byId,
  diffMarks,
  displayedPlan,
  routeRequestIds,
  sortRequestsForList,
  straightLegs,
  unassignedIndex,
} from './planView';

describe('planView', () => {
  const state = makePlanningState();

  it('shows the previous plan only when requested and available', () => {
    expect(displayedPlan(state, false)).toBe(state.plan);
    expect(displayedPlan(state, true)).toBe(state.previous_plan);
    const noPrevious = { ...state, previous_plan: null };
    expect(displayedPlan(noPrevious, true)).toBe(noPrevious.plan);
  });

  it('indexes assignments with visit order and unassigned reasons', () => {
    const index = assignmentIndex(state.plan);
    expect(index.get('50104')).toMatchObject({ engineerId: 'E01', order: 2 });
    expect(index.has('18754')).toBe(false);
    expect(unassignedIndex(state.plan).get('18754')?.reason_code).toBe('does_not_fit_window_or_shift');
  });

  it('marks diff with added before moved before time shifts', () => {
    const marks = diffMarks(state.last_diff);
    expect(marks.get('URG-001')).toBe('added');
    expect(marks.get('50104')).toBe('moved');
    expect(marks.get('46393')).toBe('shifted');
    expect(diffMarks(null).size).toBe(0);
  });

  it('builds straight legs from the engineer start through visits', () => {
    const legs = straightLegs(state.plan.routes[1], state.engineers[1], byId(state.requests));
    expect(legs.map((leg) => leg.to_request_id)).toEqual(['84627', 'URG-001']);
    expect(legs[0].coordinates[0]).toEqual([state.engineers[1].start_lon, state.engineers[1].start_lat]);
    expect(legs[0].coordinates[0]).not.toEqual([state.office.lon, state.office.lat]);
    expect(legs[1].coordinates[0]).toEqual([37.805, 55.76]);
  });

  it('sorts the list: assigned by start, then unassigned, cancelled last', () => {
    const ids = sortRequestsForList(state.requests, state.plan).map((request) => request.id);
    expect(ids).toEqual(['74198', '84627', '86160', 'URG-001', '50104', '46393', '18754', '10135']);
  });

  it('returns route order for one engineer', () => {
    expect(routeRequestIds(state.plan, 'E01')).toEqual(['74198', '86160', '50104', '46393']);
    expect(routeRequestIds(state.plan, 'E03')).toEqual([]);
  });
});
