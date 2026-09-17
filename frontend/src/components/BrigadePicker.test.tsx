import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Engineer, PlanningState, ServiceRequest } from '../api/types';
import { reassignEvent } from '../lib/events';
import { useAppStore, type AppState } from '../store/useAppStore';
import { makeEventChoice, makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { BrigadePicker } from './BrigadePicker';
import { ChoiceDialog } from './events/ChoiceDialog';

const trigger = () => screen.getByRole('button', { name: /^Бригада:/ });
const list = () => screen.getByRole('listbox', { name: 'Бригада:' });
const options = () => within(list()).getAllByRole('option');
const activeOption = () => document.getElementById(list().getAttribute('aria-activedescendant') ?? '');
const optionTexts = () => options().map((option) => option.textContent);

/** Бригада Горохов: доступна, с автомобилем и локальными работами, заявок в плане нет. */
const GOROKHOV: Engineer = {
  id: 'E04',
  name: 'Бригада Горохов',
  start_lat: 55.72,
  start_lon: 37.76,
  shift_start: '10:00',
  shift_end: '22:00',
  skills: ['local'],
  transport: 'car',
  available: true,
  unavailable_from: null,
};

/** Бригада Белузин пересела на велосипед, и добавлена Бригада Горохов: между подходящими бригадами две неподходящие. */
function stateWithGap(): PlanningState {
  const state = makePlanningState();
  const engineers = state.engineers.map((engineer) => (engineer.id === 'E02' ? { ...engineer, transport: 'bike' as const } : engineer));
  return { ...state, engineers: [...engineers, GOROKHOV] };
}

function renderPicker(requestId: string, patch: Partial<AppState> = {}, state: PlanningState = makePlanningState()) {
  const applyEvent = vi.fn().mockResolvedValue(true);
  resetStore({ datasetId: 'd_test', state, applyEvent, ...patch });
  const request = state.requests.find((item) => item.id === requestId) as ServiceRequest;
  render(<BrigadePicker request={request} />);
  return applyEvent;
}

beforeEach(() => {
  vi.resetAllMocks();
});

describe('BrigadePicker', () => {
  it('shows the brigade of the current plan on the trigger and highlights it in the list', () => {
    renderPicker('50104');
    expect(trigger()).toHaveTextContent('Бригада Арташкин');
    expect(trigger()).toHaveAttribute('aria-haspopup', 'listbox');
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();

    fireEvent.click(trigger());
    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    expect(optionTexts()).toEqual(['Бригада Арташкинв плане', 'Бригада Белузин2 заявки', 'Бригада Комарьнедоступна с 13:00']);
    const current = within(list()).getByRole('option', { selected: true });
    expect(current).toHaveTextContent('Бригада Арташкин');
    expect(current).toHaveClass('brigade-picker__option--current');
    expect(within(list()).getAllByRole('option', { selected: true })).toHaveLength(1);
    // Список открывается на бригаде текущего плана, фокус на списке.
    expect(list()).toHaveFocus();
    expect(activeOption()).toBe(current);
    expect(current).toHaveClass('brigade-picker__option--active');
  });

  it('disables brigades that cannot take the request and names the reason', () => {
    const state = makePlanningState();
    // Бригада Комарь снова доступна: у неё нет ни навыка подключения, ни автомобиля.
    const engineers = state.engineers.map((engineer) => (engineer.id === 'E03' ? { ...engineer, available: true, unavailable_from: null } : engineer));
    renderPicker('18754', {}, { ...state, engineers });
    fireEvent.click(trigger());
    expect(options()[2]).toHaveTextContent('нет навыка «Работы на подключение и дозаказы»');
    expect(options()[2]).toHaveAttribute('aria-disabled', 'true');
    expect(options()[0]).not.toHaveAttribute('aria-disabled');
    expect(options()[1]).not.toHaveAttribute('aria-disabled');
  });

  it('names the transport a brigade lacks', () => {
    const state = makePlanningState();
    const engineers = state.engineers.map((engineer) => (engineer.id === 'E03' ? { ...engineer, available: true, unavailable_from: null } : engineer));
    renderPicker('46393', {}, { ...state, engineers });
    fireEvent.click(trigger());
    expect(options()[2]).toHaveTextContent('нужен транспорт «Автомобиль»');
    expect(options()[2]).toHaveAttribute('aria-disabled', 'true');
  });

  it('counts the visits of every other brigade in the current plan', () => {
    renderPicker('18754');
    expect(trigger()).toHaveTextContent('Без бригады');
    fireEvent.click(trigger());
    expect(optionTexts()).toEqual(['Бригада Арташкин4 заявки', 'Бригада Белузин2 заявки', 'Бригада Комарьнедоступна с 13:00']);
    expect(within(list()).queryByRole('option', { selected: true })).not.toBeInTheDocument();
    // Без бригады в плане список открывается на первой подходящей.
    expect(activeOption()).toHaveTextContent('Бригада Арташкин');
  });

  it('moves with the arrows, Home and End over the brigades that can take the request only', () => {
    renderPicker('46393', {}, stateWithGap());
    fireEvent.click(trigger());
    expect(optionTexts()).toEqual([
      'Бригада Арташкинв плане',
      'Бригада Белузиннужен транспорт «Автомобиль»',
      'Бригада Комарьнедоступна с 13:00',
      'Бригада Горохов0 заявок',
    ]);
    expect(activeOption()).toHaveTextContent('Бригада Арташкин');
    fireEvent.keyDown(list(), { key: 'ArrowDown' });
    expect(activeOption()).toHaveTextContent('Бригада Горохов');
    fireEvent.keyDown(list(), { key: 'ArrowDown' });
    expect(activeOption()).toHaveTextContent('Бригада Горохов');
    fireEvent.keyDown(list(), { key: 'ArrowUp' });
    expect(activeOption()).toHaveTextContent('Бригада Арташкин');
    fireEvent.keyDown(list(), { key: 'End' });
    expect(activeOption()).toHaveTextContent('Бригада Горохов');
    fireEvent.keyDown(list(), { key: 'Home' });
    expect(activeOption()).toHaveTextContent('Бригада Арташкин');
    // Мышь над неподходящей бригадой активную строку не переносит.
    fireEvent.mouseMove(options()[1]);
    expect(activeOption()).toHaveTextContent('Бригада Арташкин');
  });

  it('opens the list from the trigger with the arrow keys', () => {
    renderPicker('50104');
    fireEvent.keyDown(trigger(), { key: 'ArrowDown' });
    expect(list()).toHaveFocus();
  });

  it('closes on Escape, returns the focus to the trigger and keeps Escape from the panels below', () => {
    renderPicker('50104');
    fireEvent.click(trigger());
    // Esc занят списком: обработчики документа ниже видят отменённое действие и панель «Почему» не закрывают.
    expect(fireEvent.keyDown(list(), { key: 'Escape' })).toBe(false);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(trigger()).toHaveFocus();
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes on a click outside the list', () => {
    renderPicker('50104');
    fireEvent.click(trigger());
    fireEvent.pointerDown(options()[1]);
    expect(list()).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('reassigns the request at the time on the clock with Enter and with a click', () => {
    const applyEvent = renderPicker('46393', { clock: '13:40' }, stateWithGap());
    fireEvent.click(trigger());
    fireEvent.keyDown(list(), { key: 'ArrowDown' });
    fireEvent.keyDown(list(), { key: 'Enter' });
    expect(applyEvent).toHaveBeenCalledTimes(1);
    expect(applyEvent).toHaveBeenCalledWith(reassignEvent('46393', 'E04', '13:40'));
    expect(applyEvent.mock.calls[0][0]).toEqual({ type: 'request_reassigned', time: '13:40', request_id: '46393', engineer_id: 'E04', request: null });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(trigger()).toHaveFocus();

    fireEvent.click(trigger());
    fireEvent.click(options()[3]);
    expect(applyEvent).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('sends nothing for the current brigade or a brigade that cannot take the request', () => {
    const applyEvent = renderPicker('50104');
    fireEvent.click(trigger());
    fireEvent.click(options()[2]);
    expect(list()).toBeInTheDocument();
    fireEvent.keyDown(list(), { key: ' ' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    fireEvent.click(trigger());
    fireEvent.click(options()[0]);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(applyEvent).not.toHaveBeenCalled();
  });

  it.each([
    ['started work', '74198', {}, 'Работа уже началась, переназначить нельзя'],
    ['a visit that started before the clock', '50104', { clock: '14:10' }, 'Работа уже началась, переназначить нельзя'],
    ['a cancelled request', '10135', {}, 'Заявка отменена'],
    ['an event being applied', '50104', { busy: true }, null],
    ['the plan before the event', '50104', { showPrevious: true }, null],
  ])('disables the trigger for %s', (_, requestId, patch, title) => {
    renderPicker(requestId, patch);
    expect(trigger()).toBeDisabled();
    if (title) expect(trigger()).toHaveAttribute('title', title);
    else expect(trigger()).not.toHaveAttribute('title');
  });

  it('disables the trigger for a request without a map point', () => {
    const state = makePlanningState();
    const requests = state.requests.map((request) => (request.id === '18754' ? { ...request, lat: null, lon: null, geocode_precision: 'none' as const } : request));
    renderPicker('18754', {}, { ...state, requests });
    expect(trigger()).toBeDisabled();
    expect(trigger()).toHaveAttribute('title', 'Адрес не найден на карте, назначить бригаду нельзя');
  });

  it('closes the open list when an event starts being applied', () => {
    renderPicker('50104');
    fireEvent.click(trigger());
    expect(list()).toBeInTheDocument();
    act(() => useAppStore.setState({ busy: true }));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('closes the open list and locks the trigger while the variants dialog is open, even without a calculation', () => {
    renderPicker('50104');
    fireEvent.click(trigger());
    expect(list()).toBeInTheDocument();
    // Часы при проигрывании дошли до «ломающего» события: окно выбора открылось без расчёта.
    act(() => useAppStore.setState({ choice: makeEventChoice() }));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(trigger()).toBeDisabled();
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    act(() => useAppStore.setState({ choice: null, choiceLoading: true }));
    expect(trigger()).toBeDisabled();
    act(() => useAppStore.setState({ choiceLoading: false }));
    expect(trigger()).toBeEnabled();
  });

  it('leaves Escape and the focus to the variants dialog that opened over the list', () => {
    const closeChoice = vi.fn();
    renderPicker('50104', { closeChoice });
    render(<ChoiceDialog />);
    fireEvent.click(trigger());
    act(() => useAppStore.setState({ choice: makeEventChoice() }));
    const dialog = screen.getByRole('dialog');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(within(dialog).getAllByRole('button', { name: 'Выбрать' })[0]).toHaveFocus();
    // Esc не достаётся закрытому списку: закрывается только окно, фокус не уходит на кнопку за ним.
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(closeChoice).toHaveBeenCalledTimes(1);
    expect(trigger()).not.toHaveFocus();
  });

  it('notes a brigade chosen by the dispatcher', () => {
    const state = makePlanningState();
    const fixed = (engineerId: string) => ({
      ...state,
      requests: state.requests.map((request) => (request.id === '50104' ? { ...request, fixed_engineer_id: engineerId } : request)),
    });
    renderPicker('50104', {}, fixed('E01'));
    expect(screen.getByText('выбрана диспетчером')).toHaveClass('muted');
  });

  it('names the chosen brigade when the plan keeps the request with another one', () => {
    const state = makePlanningState();
    const requests = state.requests.map((request) => (request.id === '50104' ? { ...request, fixed_engineer_id: 'E02' } : request));
    renderPicker('50104', {}, { ...state, requests });
    expect(screen.getByText('диспетчер выбрал: Бригада Белузин')).toBeInTheDocument();
  });

  it('has no note without a choice of the dispatcher', () => {
    renderPicker('50104');
    expect(screen.queryByText(/диспетчер/)).not.toBeInTheDocument();
  });
});
