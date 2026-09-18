import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Engineer, EventChoice, VariantOption } from '../../api/types';
import { useAppStore, type AppState } from '../../store/useAppStore';
import { reassignEvent } from '../../lib/events';
import { assignVariant } from '../../lib/variants';
import { makeEventChoice, makePlanningState, makeTimelineItem, makeUrgentChoice, makeVariantOption } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { ChoiceDialog } from './ChoiceDialog';

const dialog = () => screen.getByRole('dialog', { name: /Инженер недоступен: Бригада Белузин/ });
const card = (title: string) => within(dialog()).getByRole('article', { name: title });

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('ChoiceDialog', () => {
  it('shows three variants, highlights the recommended one and focuses its button', () => {
    useAppStore.setState({ choice: makeEventChoice() });
    render(<ChoiceDialog />);
    expect(within(dialog()).getAllByRole('article').map((item) => item.getAttribute('aria-label'))).toEqual([
      'Оптимально по дню',
      'Минимум перестановок',
      'Ничего не менять',
    ]);
    expect(card('Оптимально по дню')).toHaveClass('variant--recommended');
    expect(within(card('Оптимально по дню')).getByText('Рекомендуем')).toBeInTheDocument();
    expect(within(card('Минимум перестановок')).queryByText('Рекомендуем')).not.toBeInTheDocument();
    expect(document.activeElement).toBe(within(card('Оптимально по дню')).getByRole('button', { name: 'Выбрать' }));
    expect(within(card('Минимум перестановок')).getByText('✓ на 3 заявки меньше переезжает к другим бригадам')).toBeInTheDocument();
    expect(within(card('Ничего не менять')).getByText('✗ на 4 клиента без инженера или с опозданием больше')).toBeInTheDocument();
    // Сдвиг к плану до события: без инженера и опоздания — по +2, у неизменившихся цифр сдвига нет.
    expect(within(card('Ничего не менять')).getAllByText('+2', { selector: '.variant__delta' })).toHaveLength(2);
    expect(card('Оптимально по дню').querySelector('.variant__delta')).toBeNull();
  });

  it('chooses a variant and marks the current one', () => {
    const chooseVariant = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ choice: makeEventChoice({ current: 'stable' }), chooseVariant });
    render(<ChoiceDialog />);
    expect(within(card('Минимум перестановок')).getByRole('button', { name: 'Выбрано' })).toBeDisabled();
    fireEvent.click(within(card('Ничего не менять')).getByRole('button', { name: 'Выбрать' }));
    expect(chooseVariant).toHaveBeenCalledWith('keep');
  });

  it('closes on ✕ and on Escape without a choice', () => {
    const closeChoice = vi.fn();
    useAppStore.setState({ choice: makeEventChoice(), closeChoice });
    render(<ChoiceDialog />);
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Закрыть выбор' }));
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(closeChoice).toHaveBeenCalledTimes(2);
  });

  it('names the brigade that loses a reassigned request in the title', () => {
    // Время стоит на переназначении: план ещё до события, заявка 50104 у Бригады Арташкин.
    const event = reassignEvent('50104', 'E02', '13:00');
    const timeline = [makeTimelineItem({ id: 'tl_7', event, status: 'awaiting', choosable: true })];
    useAppStore.setState({ state: makePlanningState({ timeline }), choice: makeEventChoice({ entry_id: 'tl_7', event }) });
    render(<ChoiceDialog />);
    expect(screen.getByRole('dialog', { name: 'Переназначение заявки 50104: Бригада Арташкин → Бригада Белузин с 13:00' })).toBeInTheDocument();
  });

  it('says that variants are being computed and renders nothing when closed', () => {
    useAppStore.setState({ choiceLoading: true });
    const { rerender } = render(<ChoiceDialog />);
    expect(screen.getByRole('dialog')).toHaveTextContent('Считаем варианты…');
    useAppStore.setState({ choiceLoading: false });
    rerender(<ChoiceDialog />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

/** Бригада Зверев: аварийные работы и автомобиль, поэтому срочную заявку она взять может. */
const ZVEREV: Engineer = {
  id: 'E04',
  name: 'Бригада Зверев',
  start_lat: 55.72,
  start_lon: 37.76,
  shift_start: '10:00',
  shift_end: '22:00',
  skills: ['local', 'emergency'],
  transport: 'car',
  available: true,
  unavailable_from: null,
};

/** Бригада Соколов: вторая подходящая бригада, ей диспетчер отдаёт заявку, если цена Зверева не понравилась. */
const SOKOLOV: Engineer = { ...ZVEREV, id: 'E05', name: 'Бригада Соколов' };

const urgentDialog = () => screen.getByRole('dialog', { name: 'Срочная заявка URG-001 в 13:00' });
const urgentCard = (title: string) => within(urgentDialog()).getByRole('article', { name: title });
const list = () => screen.getByRole('listbox', { name: 'Кому отдать заявку' });
const assignCard = () => urgentCard('Отдать: Бригада Зверев');

/** Посчитанный план «отдать заявку выбранной бригаде»: сервер присылает его четвёртым вариантом. */
const assignOption = (patch: Partial<VariantOption> = {}, engineer: Engineer = ZVEREV) =>
  makeVariantOption(assignVariant(engineer.id), { title: `Отдать: ${engineer.name}`, ...patch });

const withAssign = (patch: Partial<VariantOption> = {}, engineer: Engineer = ZVEREV): EventChoice =>
  makeUrgentChoice({ variants: [...makeUrgentChoice().variants, assignOption(patch, engineer)] });

/** Окно выбора для срочной заявки: в плане её берёт Бригада Белузин, отдать её можно Бригаде Зверев или Соколову. */
function renderUrgent(choice: EventChoice = makeUrgentChoice(), patch: Partial<AppState> = {}) {
  const state = makePlanningState();
  resetStore({ datasetId: 'd_test', state: { ...state, engineers: [...state.engineers, ZVEREV, SOKOLOV] }, choice, ...patch });
  render(<ChoiceDialog />);
}

/**
 * Браузер уносит фокус с погасшей кнопки на <body>, а jsdom оставляет его на ней: уводим фокус за браузер.
 * Снять фокус с недоступной кнопки jsdom не даёт, поэтому фокус перехватывает и уносит с собой лишний элемент.
 */
function dropFocusToBody() {
  const ghost = document.createElement('button');
  document.body.append(ghost);
  ghost.focus();
  ghost.remove();
}

describe('ChoiceDialog: отдать заявку бригаде', () => {
  it('offers the fourth card for an urgent request only', () => {
    renderUrgent();
    expect(within(urgentDialog()).getAllByRole('article').map((item) => item.getAttribute('aria-label'))).toEqual([
      'Оптимально по дню',
      'Минимум перестановок',
      'Ничего не менять',
      'Другая бригада…',
    ]);
    expect(within(urgentCard('Другая бригада…')).getByText('Посчитаем план, если заявку возьмёт выбранная бригада')).toBeInTheDocument();

    // Недоступность инженера двигает целую пачку заявок: отдавать там нечего.
    resetStore({ datasetId: 'd_test', state: makePlanningState(), choice: makeEventChoice() });
    render(<ChoiceDialog />);
    expect(screen.queryByRole('article', { name: 'Другая бригада…' })).not.toBeInTheDocument();
  });

  it('names the brigade that takes the request on every card', () => {
    renderUrgent(withAssign());
    expect(within(urgentCard('Оптимально по дню')).getByText('заявку берёт Бригада Белузин')).toBeInTheDocument();
    expect(within(urgentCard('Минимум перестановок')).getByText('заявку берёт Бригада Белузин')).toBeInTheDocument();
    expect(within(urgentCard('Ничего не менять')).getByText('заявка остаётся без бригады')).toBeInTheDocument();
    expect(within(assignCard()).getByText('заявку берёт Бригада Зверев')).toBeInTheDocument();
    // У события не про одну заявку строки нет.
    resetStore({ datasetId: 'd_test', state: makePlanningState(), choice: makeEventChoice() });
    render(<ChoiceDialog />);
    expect(screen.queryByText(/заявку берёт/)).not.toBeInTheDocument();
  });

  it('opens the list of brigades and disables the ones that cannot take the request', () => {
    renderUrgent();
    fireEvent.click(within(urgentCard('Другая бригада…')).getByRole('button', { name: 'Выбрать бригаду' }));
    expect(within(list()).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Бригада Арташкиннет навыка «Аварийные работы»',
      'Бригада Белузинв плане',
      'Бригада Комарьнедоступна с 13:00',
      'Бригада Зверев0 заявок',
      'Бригада Соколов0 заявок',
    ]);
    // «В плане» отмечена бригада оптимального плана: с ней диспетчер и спорит.
    expect(within(list()).getByRole('option', { selected: true })).toHaveTextContent('Бригада Белузин');
    expect(within(list()).getAllByRole('option')[0]).toHaveAttribute('aria-disabled', 'true');
    expect(within(list()).getAllByRole('option')[2]).toHaveAttribute('aria-disabled', 'true');
    expect(within(list()).getAllByRole('option')[3]).not.toHaveAttribute('aria-disabled');
  });

  it('asks for one more plan with the chosen brigade and waits for it without closing the others', () => {
    const previewAssign = vi.fn().mockResolvedValue(undefined);
    renderUrgent(makeUrgentChoice(), { previewAssign });
    fireEvent.click(within(urgentCard('Другая бригада…')).getByRole('button', { name: 'Выбрать бригаду' }));
    fireEvent.click(within(list()).getAllByRole('option')[3]);
    expect(previewAssign).toHaveBeenCalledWith('E04');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();

    act(() => useAppStore.setState({ assignLoading: true }));
    const card = urgentCard('Другая бригада…');
    expect(within(card).getByText('Считаем план…')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Выбрать бригаду' })).toBeDisabled();
    // Окно не закрылось, остальные карточки читаются и выбираются.
    expect(within(urgentCard('Оптимально по дню')).getByRole('button', { name: 'Выбрать' })).toBeEnabled();
  });

  it('shows the price of the decision against the optimal plan and moves the focus to the new card', () => {
    renderUrgent();
    act(() => useAppStore.setState({ choice: withAssign({ moved: 2, cons: ['на 8,4 км больше пробега', 'заявка 46393 переезжает к другой бригаде'] }) }));
    const card = assignCard();
    expect(within(card).getByText('Выбор диспетчера')).toBeInTheDocument();
    expect(within(card).getByText('Цена решения против «Оптимально по дню»')).toBeInTheDocument();
    expect(within(card).getByText('✗ на 8,4 км больше пробега')).toBeInTheDocument();
    expect(within(card).queryByText(/То же, что/)).not.toBeInTheDocument();
    // Карточка с кнопкой сменилась посчитанной: фокус остаётся на выборе бригады.
    expect(within(card).getByRole('button', { name: 'Выбрать другую бригаду' })).toHaveFocus();
  });

  it('prices the decision against the optimal plan even when another variant is recommended', () => {
    // Рекомендован «Минимум перестановок», но спорит диспетчер с оптимальным планом: цена решения считается против него.
    const base = makeUrgentChoice();
    const variants = base.variants.map(
      (option): VariantOption => ({
        ...option,
        recommended: option.variant === 'stable',
        compared_to: option.variant === 'stable' ? 'keep' : 'stable',
      }),
    );
    renderUrgent({ ...base, variants: [...variants, assignOption({ cons: ['на 8,4 км больше пробега'] })] });
    expect(urgentCard('Минимум перестановок')).toHaveClass('variant--recommended');
    expect(within(assignCard()).getByText('Цена решения против «Оптимально по дню»')).toBeInTheDocument();
    expect(within(assignCard()).queryByText(/Минимум перестановок/)).not.toBeInTheDocument();
  });

  it('keeps the focus on the choice of brigade when a second brigade is picked', () => {
    // Настоящий previewAssign гасит выбор бригады тут же, в обработчике нажатия: с ним и проверяем.
    const previewAssign = vi.fn(async () => {
      useAppStore.setState({ assignLoading: true });
    });
    renderUrgent(withAssign(), { previewAssign });
    // Цена решения Зверева не понравилась: диспетчер тут же отдаёт заявку Соколову.
    fireEvent.click(within(assignCard()).getByRole('button', { name: 'Выбрать другую бригаду' }));
    fireEvent.click(within(list()).getAllByRole('option')[4]);
    expect(previewAssign).toHaveBeenCalledWith('E05');
    expect(within(assignCard()).getByRole('button', { name: 'Выбрать другую бригаду' })).toBeDisabled();

    act(dropFocusToBody);
    expect(document.body).toHaveFocus();
    act(() => useAppStore.setState({ choice: withAssign({}, SOKOLOV), assignLoading: false }));

    // Карточка та же самая, новая её не заменяет: фокус возвращается на выбор бригады сам.
    const card = urgentCard('Отдать: Бригада Соколов');
    expect(within(card).getByText('заявку берёт Бригада Соколов')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Выбрать другую бригаду' })).toHaveFocus();
  });

  it('says that the plan with the chosen brigade is the same instead of showing two empty lists', () => {
    renderUrgent(withAssign());
    expect(within(assignCard()).getByText('То же, что «Оптимально по дню»')).toBeInTheDocument();
  });

  it('applies the chosen brigade with its own variant token', () => {
    const chooseVariant = vi.fn().mockResolvedValue(true);
    renderUrgent(withAssign(), { chooseVariant });
    fireEvent.click(within(assignCard()).getByRole('button', { name: 'Выбрать' }));
    expect(chooseVariant).toHaveBeenCalledWith('assign:E04');
  });

  it('names the brigade chosen earlier when the variants are opened again without its plan', () => {
    renderUrgent(makeUrgentChoice({ current: 'assign:E04' }));
    expect(within(urgentCard('Другая бригада…')).getByText('Сейчас выбрано: Бригада Зверев')).toBeInTheDocument();
  });

  it('leaves Escape to the open list of brigades and closes the dialog with the next one', () => {
    const closeChoice = vi.fn();
    renderUrgent(makeUrgentChoice(), { closeChoice });
    fireEvent.click(within(urgentCard('Другая бригада…')).getByRole('button', { name: 'Выбрать бригаду' }));
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(closeChoice).not.toHaveBeenCalled();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(within(urgentCard('Другая бригада…')).getByRole('button', { name: 'Выбрать бригаду' })).toHaveFocus();

    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(closeChoice).toHaveBeenCalledTimes(1);
  });
});
