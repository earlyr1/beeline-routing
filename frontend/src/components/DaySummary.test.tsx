import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, moveCursor: vi.fn() };
});

import * as api from '../api/client';
import type { PlanningState } from '../api/types';
import { PLAY_TICK_MS, useAppStore, type AppState } from '../store/useAppStore';
import { makeEventChoice, makePlanningState, makeTimelineItem } from '../test/fixtures';
import { resetStore } from '../test/store';
import { DaySummary } from './DaySummary';
import { TimeBar } from './TimeBar';

/** Шкала дня фикстуры кончается в 23:00: это максимум ползунка. */
const END = '23:00';
const at = (cursor: string, patch: Partial<PlanningState> = {}) => makePlanningState({ cursor, ...patch });
const summary = () => screen.queryByRole('dialog', { name: 'Итоги дня' });
/** Часы и план стоят на этом времени, как после ответа сервера. */
const moveTo = (time: string, patch: Partial<AppState> = {}) =>
  act(() => useAppStore.setState({ state: at(time), clock: time, ...patch }));

beforeEach(() => {
  vi.resetAllMocks();
  resetStore({ datasetId: 'd_test', state: at('13:00') });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('DaySummary', () => {
  it('drops once when the clock reaches the end of the day and again only after the clock went back', () => {
    render(<DaySummary />);
    expect(summary()).toBeNull();

    moveTo(END);
    expect(summary()).toBeInTheDocument();
    fireEvent.click(within(summary() as HTMLElement).getByRole('button', { name: 'Закрыть' }));
    expect(summary()).toBeNull();

    // Часы в конце, а план сменился (событие в конце дня, опрос шкалы): закрытые итоги сами не возвращаются.
    act(() => useAppStore.setState({ state: at(END, { version: 9 }), busy: true }));
    act(() => useAppStore.setState({ busy: false }));
    expect(summary()).toBeNull();

    moveTo('20:00');
    expect(summary()).toBeNull();
    moveTo(END);
    expect(summary()).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(summary()).toBeNull();
  });

  it('waits for the plan at the end of the day before dropping', () => {
    render(<DaySummary />);
    // Ползунок дотянули до конца, но план ещё на 13:00: итоги по нему были бы не итогами дня.
    act(() => useAppStore.setState({ clock: END, dragging: true }));
    expect(summary()).toBeNull();
    act(() => useAppStore.setState({ dragging: false, committing: true }));
    expect(summary()).toBeNull();
    act(() => useAppStore.setState({ state: at(END), committing: false }));
    expect(summary()).toBeInTheDocument();
  });

  it('does not drop over the choice of a variant and drops after the choice when the day is over', () => {
    render(<DaySummary />);
    const pending = makeEventChoice({ entry_id: 'tl_9' });
    act(() => useAppStore.setState({ state: at(END, { pending_choice: pending }), clock: END, choice: pending }));
    expect(summary()).toBeNull();
    // Варианты события, открытые с метки в конце дня: окно выбора тоже главнее итогов.
    act(() => useAppStore.setState({ state: at(END), choice: null, choiceLoading: true }));
    expect(summary()).toBeNull();
    act(() => useAppStore.setState({ choiceLoading: false }));
    expect(summary()).toBeInTheDocument();
  });

  it('does not drop for a plan that opens already at the end of the day', () => {
    resetStore({ datasetId: 'd_test', state: at(END) });
    render(<DaySummary />);
    expect(summary()).toBeNull();
    moveTo('22:00');
    moveTo(END);
    expect(summary()).toBeInTheDocument();
  });

  it('shows the morning against the end of the day, the comparison, the events and the calls', () => {
    const timeline = [
      makeTimelineItem({ id: 'tl_1', variant: 'optimal' }),
      makeTimelineItem({
        id: 'tl_2',
        event: { type: 'engineer_unavailable', time: '13:00', request: null, request_id: null, engineer_id: 'E03' },
        variant: 'keep',
        variant_auto: true,
      }),
    ];
    resetStore({ datasetId: 'd_test', state: at(END, { timeline }), daySummaryOpen: true });
    render(<DaySummary />);
    const dialog = summary() as HTMLElement;
    expect(within(dialog).getByText('Восток')).toBeInTheDocument();
    expect(within(dialog).getByText('Утро — план на начало дня, до событий. Итог — план на 23:00.')).toBeInTheDocument();
    const cells = (label: string) =>
      Array.from((within(dialog).getByText(label).closest('tr') as HTMLTableRowElement).cells).map((cell) => cell.textContent);
    expect(cells('Суммарный пробег')).toEqual(['Суммарный пробег', '41,5 км', '34,9 км', '−6,6 км']);
    expect(cells('Не назначено')).toEqual(['Не назначено', '0', '1', '+1']);
    expect(within(dialog).getByText('из них перенесено со звонком клиенту').closest('tr')).toHaveClass('day-summary__nested');
    expect(within(dialog).getByText('−6,6 км')).toHaveClass('delta--better');
    expect(within(dialog).getByText(/^Итог дня к базовому \(FCFS\)/).closest('p')).toHaveTextContent(
      'Итог дня к базовому (FCFS): инженеров 0, пробег +2,7 км, не назначено −1 · к диспетчерам Билайна (исходный день, события в нём не учтены): инженеров −1, пробег −5,1 км, не назначено +1',
    );
    expect(within(dialog).getByText('пробег +2,7 км')).toHaveClass('delta--worse');

    const metric = (label: string) => within(dialog).getByText(label).closest('.metric') as HTMLElement;
    expect(metric('Отмены')).toHaveTextContent('Отмены1');
    expect(metric('События бригад')).toHaveTextContent('События бригад1');
    expect(metric('Звонки')).toHaveTextContent('Звонки0');
    expect(within(dialog).getByText('с окном выбора: 1').parentElement).toHaveTextContent(
      'Всего применено: 2 · с окном выбора: 1 · без него: 1 — выбирать было не из чего',
    );
    // 18754 так и осталась без бригады, а клиенту не позвонили: это недоделка дня.
    expect(metric('Согласовано')).toHaveTextContent('Согласовано0');
    expect(metric('Ждут звонка')).toHaveClass('metric--warn');
    expect(within(dialog).getByText('Недоделка дня: 1 клиент ещё ждёт звонка — вкладка «Коммуникации».')).toHaveClass('note');
    expect(within(dialog).getByRole('button', { name: 'Закрыть' })).toHaveFocus();
  });

  it('says the day went without events and nobody waits for a call', () => {
    const agreed = { '18754': { window: null, entry_id: 'tl_8', applied: true } };
    resetStore({ datasetId: 'd_test', state: at(END), daySummaryOpen: true, agreed });
    render(<DaySummary />);
    const dialog = summary() as HTMLElement;
    expect(within(dialog).getByText('Событий за день не было: итог — утренний план.')).toBeInTheDocument();
    expect(within(dialog).getByText('Ждут звонка').closest('.metric')).not.toHaveClass('metric--warn');
    expect(within(dialog).queryByText(/Недоделка дня/)).toBeNull();
  });

  it('opens the comparison tab and closes', () => {
    resetStore({ datasetId: 'd_test', state: at(END), daySummaryOpen: true });
    render(<DaySummary />);
    fireEvent.click(screen.getByRole('button', { name: 'Открыть «Сравнение»' }));
    expect(useAppStore.getState()).toMatchObject({ activeTab: 'comparison', daySummaryOpen: false });
    expect(summary()).toBeNull();
  });

  it('drops after the slider is released at the end and opens again from the clock', async () => {
    vi.mocked(api.moveCursor).mockResolvedValue(at(END, { version: 5 }));
    render(
      <>
        <TimeBar />
        <DaySummary />
      </>,
    );
    expect(screen.queryByRole('button', { name: 'Итоги дня' })).toBeNull();
    const slider = screen.getByRole('slider', { name: 'Текущее время' });
    fireEvent.pointerDown(slider, { pointerId: 1 });
    fireEvent.change(slider, { target: { value: '1380' } });
    expect(summary()).toBeNull();
    fireEvent.pointerUp(slider, { pointerId: 1 });
    await waitFor(() => expect(summary()).toBeInTheDocument());
    expect(vi.mocked(api.moveCursor).mock.calls).toEqual([['d_test', END]]);

    fireEvent.click(screen.getByRole('button', { name: 'Закрыть итоги' }));
    expect(summary()).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Итоги дня' }));
    expect(summary()).toBeInTheDocument();
  });

  it('drops when the playback stops at the end of the day', async () => {
    vi.useFakeTimers();
    resetStore({ datasetId: 'd_test', state: at('22:55') });
    vi.mocked(api.moveCursor).mockResolvedValue(at(END, { version: 5 }));
    render(
      <>
        <TimeBar />
        <DaySummary />
      </>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Запустить' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 4);
    });
    expect(summary()).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 3);
    });
    expect(useAppStore.getState()).toMatchObject({ playing: false, clock: END });
    expect(summary()).toBeInTheDocument();
  });
});
