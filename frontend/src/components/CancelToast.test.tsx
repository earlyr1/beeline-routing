import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, addTimelineEvent: vi.fn(), moveCursor: vi.fn(), startScenario: vi.fn() };
});

import * as api from '../api/client';
import type { PlanningState } from '../api/types';
import { cancelEvent } from '../lib/events';
import { CANCEL_UNDO_MS, useAppStore } from '../store/useAppStore';
import { makeDataUrgentState, makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { CancelToast } from './CancelToast';
import { RequestsTab } from './panel/RequestsTab';

const rowOf = (label: string) => screen.getByText(label).closest('li') as HTMLElement;
const cancelIn = (label: string) => fireEvent.click(within(rowOf(label)).getByRole('button', { name: 'Отменить' }));
const confirmButton = () => screen.queryByRole('button', { name: /^Подтвердить отмену заявки/ });
const undoButton = () => screen.queryByRole('button', { name: /^Не отменять заявку/ });
const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

function renderDay(state: PlanningState = makePlanningState()) {
  resetStore({ datasetId: 'd_test', state });
  render(
    <>
      <RequestsTab />
      <CancelToast />
    </>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  localStorage.clear();
  sessionStorage.clear();
  // Сервер отвечает тем же планом; часы, переведённые вперёд, он переводит на их время.
  vi.mocked(api.addTimelineEvent).mockImplementation(async () => useAppStore.getState().state!);
  vi.mocked(api.moveCursor).mockImplementation(async (_dataset, time) => ({ ...useAppStore.getState().state!, cursor: time }));
});

afterEach(() => {
  vi.useRealTimers();
});

describe('CancelToast', () => {
  it('sends nothing at the click and shows «Заявка … отменена» with ✓, ✕ and a 5-second countdown', async () => {
    renderDay();
    cancelIn('50104');

    expect(api.addTimelineEvent).not.toHaveBeenCalled();
    expect(api.moveCursor).not.toHaveBeenCalled();
    expect(screen.getByText('Заявка 50104 отменена')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Заявка 50104 отменена. Через 5 секунд отмена применится, Esc — не отменять.');
    expect(confirmButton()).toHaveAccessibleName('Подтвердить отмену заявки 50104');
    expect(undoButton()).toHaveAccessibleName('Не отменять заявку 50104');
    expect(document.querySelector('.toast__count')).toHaveTextContent('5');

    await advance(2000);
    expect(document.querySelector('.toast__count')).toHaveTextContent('3');
    expect(api.addTimelineEvent).not.toHaveBeenCalled();
  });

  it('names an urgent request of the day with the URG- prefix', () => {
    renderDay(makeDataUrgentState());
    cancelIn('URG-50104');
    expect(screen.getByText('Заявка URG-50104 отменена')).toBeInTheDocument();
  });

  it('applies the cancel on ✓ once, with the time the clock showed at the click', async () => {
    renderDay();
    cancelIn('50104');
    // За секунды уведомления часы ушли дальше: событие всё равно встаёт на время клика.
    act(() => useAppStore.getState().setClock('13:20'));
    fireEvent.click(confirmButton()!);
    await advance(0);

    expect(api.addTimelineEvent).toHaveBeenCalledTimes(1);
    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', cancelEvent('50104', '13:00'), undefined);
    expect(confirmButton()).toBeNull();
    expect(screen.queryByText('Заявка 50104 отменена')).toBeNull();

    await advance(CANCEL_UNDO_MS);
    expect(api.addTimelineEvent).toHaveBeenCalledTimes(1);
  });

  it('applies the cancel by itself when the countdown reaches zero and disappears', async () => {
    renderDay();
    cancelIn('50104');
    await advance(CANCEL_UNDO_MS - 1);
    expect(api.addTimelineEvent).not.toHaveBeenCalled();

    await advance(1);
    expect(api.addTimelineEvent).toHaveBeenCalledTimes(1);
    expect(api.addTimelineEvent).toHaveBeenCalledWith('d_test', cancelEvent('50104', '13:00'), undefined);
    expect(screen.queryByText('Заявка 50104 отменена')).toBeNull();
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  });

  it.each([
    ['✕', () => fireEvent.click(undoButton()!)],
    ['Esc', () => fireEvent.keyDown(document.body, { key: 'Escape' })],
  ])('drops the cancel on %s: nothing reaches the server', async (_, undo) => {
    renderDay();
    cancelIn('50104');
    undo();

    expect(screen.queryByText('Заявка 50104 отменена')).toBeNull();
    await advance(CANCEL_UNDO_MS * 2);
    expect(api.addTimelineEvent).not.toHaveBeenCalled();
    expect(api.moveCursor).not.toHaveBeenCalled();
    expect(useAppStore.getState().pendingCancel).toBeNull();
    expect(within(rowOf('50104')).getByRole('button', { name: 'Отменить' })).toBeEnabled();
  });

  it('leaves Esc to an open dialog', async () => {
    renderDay();
    cancelIn('50104');
    act(() => useAppStore.setState({ editingRequestId: '46393' }));
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(screen.getByText('Заявка 50104 отменена')).toBeInTheDocument();
  });

  it('applies the pending cancel at once when a second one starts, then counts down the second', async () => {
    renderDay();
    cancelIn('50104');
    await advance(1000);
    cancelIn('46393');
    await advance(0);

    expect(api.addTimelineEvent).toHaveBeenCalledTimes(1);
    expect(api.addTimelineEvent).toHaveBeenLastCalledWith('d_test', cancelEvent('50104', '13:00'), undefined);
    expect(screen.queryByText('Заявка 50104 отменена')).toBeNull();
    expect(screen.getByText('Заявка 46393 отменена')).toBeInTheDocument();
    expect(document.querySelector('.toast__count')).toHaveTextContent('5');

    await advance(CANCEL_UNDO_MS);
    expect(api.addTimelineEvent).toHaveBeenCalledTimes(2);
    expect(api.addTimelineEvent).toHaveBeenLastCalledWith('d_test', cancelEvent('46393', '13:00'), undefined);
  });

  it('keeps counting when the same request is cancelled again during its notice', async () => {
    renderDay();
    cancelIn('50104');
    await advance(3000);
    cancelIn('50104');
    await advance(2000);
    expect(api.addTimelineEvent).toHaveBeenCalledTimes(1);
  });

  it('drops the pending cancel when the page goes away and keeps nothing of it in the browser', async () => {
    renderDay();
    cancelIn('50104');
    expect(Object.keys(localStorage).concat(Object.keys(sessionStorage)).join()).not.toMatch(/cancel/i);

    act(() => {
      window.dispatchEvent(new Event('pagehide'));
    });
    expect(useAppStore.getState().pendingCancel).toBeNull();
    await advance(CANCEL_UNDO_MS * 2);
    expect(api.addTimelineEvent).not.toHaveBeenCalled();
  });

  it('drops the pending cancel when another day is opened', async () => {
    vi.mocked(api.startScenario).mockReturnValue(new Promise(() => {}));
    renderDay();
    cancelIn('50104');
    act(() => void useAppStore.getState().startScenario('east'));

    expect(useAppStore.getState().pendingCancel).toBeNull();
    await advance(CANCEL_UNDO_MS * 2);
    expect(api.addTimelineEvent).not.toHaveBeenCalled();
  });
});
