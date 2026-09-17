import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { makeEventChoice, makePlanningState } from '../../test/fixtures';
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

  it('says that variants are being computed and renders nothing when closed', () => {
    useAppStore.setState({ choiceLoading: true });
    const { rerender } = render(<ChoiceDialog />);
    expect(screen.getByRole('dialog')).toHaveTextContent('Считаем варианты…');
    useAppStore.setState({ choiceLoading: false });
    rerender(<ChoiceDialog />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
