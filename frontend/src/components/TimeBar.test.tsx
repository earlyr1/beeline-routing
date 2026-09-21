import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    moveCursor: vi.fn(),
    deleteTimelineEvent: vi.fn(),
    addTimelineEvent: vi.fn(),
    getPlanningState: vi.fn(),
    postEvent: vi.fn(),
    buildPlan: vi.fn(),
  };
});

import * as api from '../api/client';
import type { PlanningState } from '../api/types';
import { pinLeft } from '../lib/timeBar';
import { PLAY_TICK_MS, useAppStore } from '../store/useAppStore';
import { makeEventChoice, makePlanningState, makeReassignEvent, makeTimeline, makeTimelineItem, makeUrgentChoice } from '../test/fixtures';
import { resetStore } from '../test/store';
import { TimeBar } from './TimeBar';

const at = (cursor: string, patch: Partial<PlanningState> = {}) => makePlanningState({ cursor, ...patch });
const slider = () => screen.getByRole('slider', { name: 'Текущее время' }) as HTMLInputElement;
const clockLabel = () => screen.getByRole('timer', { name: 'Время на часах' });
/** Любой запрос к серверу, кроме указанных. */
const otherRequests = () =>
  [api.addTimelineEvent, api.deleteTimelineEvent, api.getPlanningState, api.postEvent, api.buildPlan].filter((mock) => vi.mocked(mock).mock.calls.length > 0);

beforeEach(() => {
  vi.resetAllMocks();
  resetStore({ datasetId: 'd_test', state: at('13:00') });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('TimeBar', () => {
  it('shows the clock, the tempo and a slider over the day scale', () => {
    render(<TimeBar />);
    expect(screen.getByRole('region', { name: 'Время дня' })).toBeInTheDocument();
    expect(clockLabel()).toHaveTextContent('13:00');
    expect(screen.getByText('1 ч = 6 с')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Запустить' })).toBeEnabled();
    expect(slider()).toHaveAttribute('min', '540');
    expect(slider()).toHaveAttribute('max', '1380');
    expect(slider()).toHaveAttribute('step', '1');
    expect(slider().value).toBe('780');
  });

  it('keeps the slider at the start of the scale while the label shows an earlier clock', () => {
    resetStore({ datasetId: 'd_test', state: at('00:00') });
    render(<TimeBar />);
    expect(clockLabel()).toHaveTextContent('00:00');
    expect(slider().value).toBe('540');
  });

  it('widens the slider below 09:00 when an event of the day is earlier', () => {
    const early = makeTimelineItem({ event: { type: 'cancel', time: '06:15', request: null, request_id: '50104', engineer_id: null } });
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline: [early] }) });
    render(<TimeBar />);
    expect(slider()).toHaveAttribute('min', '360');
  });

  it('plays one hour of the day in six seconds and commits the clock on pause', async () => {
    vi.useFakeTimers();
    vi.mocked(api.moveCursor).mockResolvedValue(at('14:00', { version: 5 }));
    render(<TimeBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Запустить' }));
    expect(screen.getByRole('button', { name: 'Пауза' })).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(clockLabel()).toHaveTextContent('14:00');
    expect(slider().value).toBe('840');
    expect(api.moveCursor).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Пауза' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 5);
    });
    expect(screen.getByRole('button', { name: 'Запустить' })).toBeInTheDocument();
    expect(clockLabel()).toHaveTextContent('14:00');
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', '14:00']]);
  });

  it('moves the clock while dragging without asking the server and commits once on release', async () => {
    render(<TimeBar />);
    fireEvent.pointerDown(slider(), { pointerId: 1 });
    expect(useAppStore.getState().dragging).toBe(true);
    for (const value of ['800', '860', '900']) fireEvent.change(slider(), { target: { value } });
    expect(clockLabel()).toHaveTextContent('15:00');
    expect(api.moveCursor).not.toHaveBeenCalled();
    expect(otherRequests()).toEqual([]);

    vi.mocked(api.moveCursor).mockResolvedValue(at('15:00', { version: 5 }));
    fireEvent.pointerUp(slider(), { pointerId: 1 });
    expect(useAppStore.getState().dragging).toBe(false);
    await waitFor(() => expect(useAppStore.getState().state?.version).toBe(5));
    fireEvent.lostPointerCapture(slider(), { pointerId: 1 });
    fireEvent.keyUp(slider(), { key: 'ArrowRight' });
    fireEvent.blur(slider());
    await act(async () => undefined);
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', '15:00']]);
    expect(otherRequests()).toEqual([]);
  });

  it('pauses the playback when the dispatcher grabs the slider', async () => {
    vi.useFakeTimers();
    resetStore({ datasetId: 'd_test', state: at('13:00') });
    render(<TimeBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Запустить' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 3);
    });
    fireEvent.pointerDown(slider(), { pointerId: 1 });
    expect(useAppStore.getState()).toMatchObject({ playing: false, dragging: true, clock: '13:03' });
    expect(api.moveCursor).not.toHaveBeenCalled();
  });

  it('commits a keyboard step and does nothing more on blur when the plan is already at the clock', async () => {
    vi.mocked(api.moveCursor).mockResolvedValue(at('13:01', { version: 5 }));
    render(<TimeBar />);
    fireEvent.change(slider(), { target: { value: '781' } });
    fireEvent.keyUp(slider(), { key: 'ArrowRight' });
    await waitFor(() => expect(useAppStore.getState().state?.version).toBe(5));
    fireEvent.blur(slider());
    await act(async () => undefined);
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', '13:01']]);
  });

  it('draws one pin per minute coloured by status with the description and the status in the title', () => {
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline: makeTimeline() }) });
    const { container } = render(<TimeBar />);
    expect(Array.from(container.querySelectorAll('.time-bar__pin')).map((pin) => pin.textContent)).toEqual([
      'Отмена',
      'Недоступен2',
      'Задержка',
      'Отмена',
    ]);
    const pending = screen.getByTitle('Задержка: Бригада Арташкин на 150 мин с 15:00 · впереди');
    expect(pending).toHaveClass('time-bar__pin', 'time-bar__pin--pending');
    expect(pending).toHaveStyle({ left: `${pinLeft({ min: 540, max: 1380 }, 900)}%` });
    expect(screen.getByTitle('Отмена заявки 74198 в 16:30 · отклонено: Заявка 74198 уже выполнена.')).toHaveClass('time-bar__pin--rejected');
    expect(screen.getByTitle('Отмена заявки 10135 в 09:30 · применено')).toHaveClass('time-bar__pin--applied');
    const group = screen.getByRole('button', { name: /^Недоступен\s*2$/ });
    expect(group).toHaveClass('time-bar__pin--applied');
    expect(group).toHaveAttribute('title', 'Инженер недоступен: Бригада Комарь с 13:00 · применено\nСрочная заявка URG-001 в 13:00 · применено');
  });

  it('lists the events of a pin with their status and deletes one of them', async () => {
    const timeline = makeTimeline();
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline }) });
    vi.mocked(api.deleteTimelineEvent).mockResolvedValue(at('13:00', { version: 5, timeline: timeline.filter((item) => item.id !== 'tl_3') }));
    render(<TimeBar />);

    fireEvent.click(screen.getByTitle(/^Отмена заявки 74198/));
    const rejected = screen.getByRole('group', { name: 'События 16:30' });
    expect(within(rejected).getByText('Отмена заявки 74198 в 16:30')).toBeInTheDocument();
    expect(within(rejected).getByText('отклонено: Заявка 74198 уже выполнена.')).toBeInTheDocument();
    fireEvent.click(within(rejected).getByRole('button', { name: 'Закрыть события' }));
    expect(screen.queryByRole('group', { name: 'События 16:30' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle(/^Инженер недоступен: Бригада Комарь с 13:00/));
    const popover = screen.getByRole('group', { name: 'События 13:00' });
    const items = within(popover).getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText('Инженер недоступен: Бригада Комарь с 13:00')).toBeInTheDocument();
    expect(within(items[1]).getByText('Срочная заявка URG-001 в 13:00')).toBeInTheDocument();
    expect(within(items[1]).getByText('применено')).toBeInTheDocument();

    fireEvent.click(within(items[1]).getByRole('button', { name: 'Удалить событие' }));
    await waitFor(() => expect(screen.queryByRole('group', { name: 'События 13:00' })).not.toBeInTheDocument());
    expect(api.deleteTimelineEvent).toHaveBeenCalledWith('d_test', 'tl_3');
    expect(screen.getByTitle('Инженер недоступен: Бригада Комарь с 13:00 · применено')).toBeInTheDocument();
  });

  it.each([
    ['replanning', { busy: true }],
    ['moving the plan to the clock', { committing: true }],
    ['playing', { playing: true }],
    ['the choice of a variant is open', { choiceLoading: true }],
  ])('does not delete an event while %s', (_, patch) => {
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline: makeTimeline() }), ...patch });
    render(<TimeBar />);
    fireEvent.click(screen.getByTitle(/^Задержка: Бригада Арташкин/));
    expect(within(screen.getByRole('group', { name: 'События 15:00' })).getByRole('button', { name: 'Удалить событие' })).toBeDisabled();
  });

  it('opens the request card from an event about a request that is already in the plan', () => {
    const [, , urgent] = makeTimeline();
    const ahead = makeTimelineItem({
      id: 'tl_6',
      event: { ...urgent.event, time: '18:00', request: { ...urgent.event.request!, id: 'URG-404' } },
      status: 'pending',
    });
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline: [...makeTimeline(), ahead] }), selectedEngineerId: 'E01' });
    render(<TimeBar />);

    fireEvent.click(screen.getByTitle(/^Инженер недоступен: Бригада Комарь с 13:00/));
    const popover = screen.getByRole('group', { name: 'События 13:00' });
    // Событие об инженере ссылкой не становится.
    expect(within(popover).queryByRole('button', { name: /^Инженер недоступен/ })).not.toBeInTheDocument();
    fireEvent.click(within(popover).getByRole('button', { name: 'Срочная заявка URG-001 в 13:00' }));
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: 'URG-001', selectedEngineerId: 'E01' });
    expect(screen.queryByRole('group', { name: 'События 13:00' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle(/^Отмена заявки 74198/));
    expect(within(screen.getByRole('group', { name: 'События 16:30' })).getByRole('button', { name: 'Отмена заявки 74198 в 16:30' })).toBeInTheDocument();

    // Срочной заявки впереди ещё нет в плане: открыть нечего.
    fireEvent.click(screen.getByTitle(/^Срочная заявка URG-404/));
    const later = screen.getByRole('group', { name: 'События 18:00' });
    expect(within(later).getByText('Срочная заявка URG-404 в 18:00')).toBeInTheDocument();
    expect(within(later).queryByRole('button', { name: /^Срочная заявка/ })).not.toBeInTheDocument();
  });

  it('offers «Варианты…» for an event that breaks the plan and shows its choice', () => {
    const openChoice = vi.fn().mockResolvedValue(undefined);
    const timeline = [
      makeTimelineItem({ id: 'tl_2', event: makeEventChoice().event, status: 'applied', choosable: true, variant: 'stable' }),
      makeTimelineItem({ id: 'tl_3', status: 'applied' }),
    ];
    resetStore({ datasetId: 'd_test', state: makePlanningState({ timeline }), openChoice });
    render(<TimeBar />);
    fireEvent.click(screen.getByRole('button', { name: /Недоступен/ }));
    const events = screen.getByRole('group', { name: 'События 13:00' });
    expect(within(events).getByText('Вариант: Минимум перестановок')).toBeInTheDocument();
    fireEvent.click(within(events).getByRole('button', { name: 'Варианты…' }));
    expect(openChoice).toHaveBeenCalledWith('tl_2');
    expect(screen.queryByRole('group', { name: 'События 13:00' })).not.toBeInTheDocument();
  });

  it('names the variant «отдать бригаде» by the brigade that took the request', () => {
    const timeline = [makeTimelineItem({ id: 'tl_3', event: makeUrgentChoice().event, status: 'applied', choosable: true, variant: 'assign:E02' })];
    resetStore({ datasetId: 'd_test', state: makePlanningState({ timeline }) });
    render(<TimeBar />);
    fireEvent.click(screen.getByRole('button', { name: /Срочная/ }));
    expect(within(screen.getByRole('group', { name: 'События 13:00' })).getByText('Вариант: Отдать: Бригада Белузин')).toBeInTheDocument();
  });

  it('names the kept variant of a reassignment as the insertion into the route and opens its request', () => {
    const timeline = [makeTimelineItem({ id: 'tl_4', event: makeReassignEvent(), status: 'applied', choosable: true, variant: 'keep' })];
    resetStore({ datasetId: 'd_test', state: makePlanningState({ timeline }) });
    render(<TimeBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Назначение' }));
    const events = screen.getByRole('group', { name: 'События 13:30' });
    expect(within(events).getByText('Вариант: Вставить в маршрут')).toBeInTheDocument();
    fireEvent.click(within(events).getByRole('button', { name: 'Переназначение заявки 50104: Бригада Арташкин → Бригада Белузин с 13:30' }));
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
  });

  it('shows the strategy of a cancellation, which has no choice window of its own', () => {
    // Отмену диспетчер выбирал кнопкой «Отменить, маршруты не трогать»: на шкале должно быть видно, что он выбрал.
    const timeline = [makeTimelineItem({ id: 'tl_1', status: 'applied', variant: 'keep' })];
    resetStore({ datasetId: 'd_test', state: makePlanningState({ timeline }) });
    render(<TimeBar />);
    fireEvent.click(screen.getByTitle(/^Отмена заявки 10135/));
    const events = screen.getByRole('group', { name: 'События 09:30' });
    expect(within(events).getByText('Вариант: Маршруты не трогать')).toBeInTheDocument();
    // Окна выбора у отмены нет: кнопку «Варианты…» ей не показывают.
    expect(within(events).queryByRole('button', { name: 'Варианты…' })).not.toBeInTheDocument();
  });

  it('blocks play and the slider while the choice is open', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), choice: makeEventChoice() });
    render(<TimeBar />);
    expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
    expect(screen.getByRole('slider', { name: 'Текущее время' })).toBeDisabled();
  });

  it('closes the events of a pin when the choice opens above them and while it waits for the variants', () => {
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline: makeTimeline() }) });
    render(<TimeBar />);
    fireEvent.click(screen.getByTitle(/^Задержка: Бригада Арташкин/));
    expect(screen.getByRole('group', { name: 'События 15:00' })).toBeInTheDocument();
    act(() => useAppStore.setState({ choice: makeEventChoice() }));
    expect(screen.queryByRole('group', { name: 'События 15:00' })).not.toBeInTheDocument();

    act(() => useAppStore.setState({ choice: null }));
    fireEvent.click(screen.getByTitle(/^Задержка: Бригада Арташкин/));
    expect(screen.getByRole('group', { name: 'События 15:00' })).toBeInTheDocument();
    act(() => useAppStore.setState({ choiceLoading: true }));
    expect(screen.queryByRole('group', { name: 'События 15:00' })).not.toBeInTheDocument();
  });

  it('closes the events of a pin on Esc and keeps that Esc from the panels below', () => {
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline: makeTimeline() }) });
    render(<TimeBar />);
    fireEvent.click(screen.getByTitle(/^Задержка: Бригада Арташкин/));
    // false — Esc отменён: панель «Почему» под списком его не получит.
    expect(fireEvent.keyDown(document.body, { key: 'Escape' })).toBe(false);
    expect(screen.queryByRole('group', { name: 'События 15:00' })).not.toBeInTheDocument();
    // Список закрыт: следующий Esc часы не забирают.
    expect(fireEvent.keyDown(document.body, { key: 'Escape' })).toBe(true);
  });

  it('says when the plan is being moved to the clock and while the server prepares the events', () => {
    resetStore({ datasetId: 'd_test', state: at('13:00', { timeline_ready: false }) });
    render(<TimeBar />);
    expect(screen.getByText('Готовим события…')).toHaveClass('muted');
    act(() => useAppStore.setState({ committing: true }));
    expect(screen.getByText('Пересчитываем план…')).toHaveClass('muted');
    expect(screen.queryByText('Готовим события…')).not.toBeInTheDocument();
    act(() => useAppStore.setState({ committing: false, state: at('13:00') }));
    expect(screen.queryByText(/Пересчитываем план|Готовим события/)).not.toBeInTheDocument();
  });

  it('stops the clock when the time bar goes away', async () => {
    vi.useFakeTimers();
    const view = render(<TimeBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Запустить' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 2);
    });
    view.unmount();
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 5);
    expect(useAppStore.getState()).toMatchObject({ playing: false, clock: '13:02' });
    expect(api.moveCursor).not.toHaveBeenCalled();
  });
});
