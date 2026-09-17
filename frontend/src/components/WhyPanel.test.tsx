import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, getExplanation: vi.fn() };
});

import * as api from '../api/client';
import { useAppStore } from '../store/useAppStore';
import { makeExplanation, makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { ExplanationCard } from './ExplanationCard';
import { WhyPanel } from './WhyPanel';

const panel = () => screen.getByRole('complementary', { name: 'Почему' });
const listItems = (name: string) =>
  within(within(panel()).getByRole('list', { name })).getAllByRole('listitem').map((item) => item.textContent);

beforeEach(() => {
  vi.resetAllMocks();
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('WhyPanel', () => {
  it('is closed until «Почему?» is pressed', () => {
    useAppStore.setState({ selectedRequestId: '50104' });
    render(<WhyPanel />);
    expect(screen.queryByRole('complementary', { name: 'Почему' })).not.toBeInTheDocument();
  });

  it('explains the choice for the open request: checks, factors and other engineers', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    useAppStore.setState({ selectedRequestId: '50104', whyOpen: true });
    render(<WhyPanel />);

    expect(await within(panel()).findByText('Проверки')).toBeInTheDocument();
    expect(within(panel()).getByRole('heading', { name: 'Заявка 50104' })).toBeInTheDocument();
    expect(within(panel()).getByText('Почему так')).toBeInTheDocument();
    expect(within(panel()).getByText('Временное окно')).toBeInTheDocument();
    expect(listItems('Почему такой выбор')).toEqual(['Не нужен дополнительный инженер', 'Самая короткая вставка в маршрут']);
    expect(within(panel()).getByText('Бригада Белузин').closest('tr')).toHaveTextContent('+2,1 км');
  });

  it('opens the brigade page from the table of other engineers and follows it', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    useAppStore.setState({ selectedRequestId: '50104', whyOpen: true });
    render(<WhyPanel />);

    const table = (await within(panel()).findByRole('table')) as HTMLElement;
    fireEvent.click(within(table).getByRole('button', { name: 'Бригада Комарь' }));
    // Панель остаётся открытой и переходит к маршруту выбранной бригады.
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: null, selectedEngineerId: 'E03', whyOpen: true });
    expect(within(panel()).getByRole('heading', { name: 'Бригада Комарь' })).toBeInTheDocument();
    expect(listItems('Почему такой маршрут')).toEqual(['В этом плане у инженера нет визитов.', 'Инженер недоступен с 13:00.']);
  });

  it('explains the route of the open brigade in dispatcher language', () => {
    useAppStore.setState({ selectedEngineerId: 'E01', whyOpen: true });
    render(<WhyPanel />);
    expect(within(panel()).getByText('Почему такой маршрут')).toBeInTheDocument();
    expect(listItems('Почему такой маршрут')).toEqual([
      'Маршрут 23,7 км — 68% пробега всего плана; порядок визитов следует окнам заявок.',
      'Все визиты начинаются внутри окон, минимальный запас до конца окна 1 ч 50 мин (заявка 46393).',
      'Работы заканчиваются в 15:55, до конца смены в 22:00 остаётся 6 ч 5 мин.',
      'Визиты 74198, 86160 начаты до события и закреплены: перепланирование их не меняет.',
    ]);
  });

  it('prefers the open request card over the brigade page, like the right column', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    useAppStore.setState({ selectedEngineerId: 'E01', selectedRequestId: '50104', whyOpen: true });
    render(<WhyPanel />);
    expect(await within(panel()).findByText('Проверки')).toBeInTheDocument();
    expect(within(panel()).getByRole('heading', { name: 'Заявка 50104' })).toBeInTheDocument();
  });

  it('closes on ✕ and on Escape', () => {
    useAppStore.setState({ selectedEngineerId: 'E01', whyOpen: true });
    render(<WhyPanel />);
    fireEvent.click(within(panel()).getByRole('button', { name: 'Закрыть «Почему»' }));
    expect(useAppStore.getState().whyOpen).toBe(false);

    act(() => useAppStore.getState().toggleWhy());
    expect(panel()).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(useAppStore.getState().whyOpen).toBe(false);
    expect(screen.queryByRole('complementary', { name: 'Почему' })).not.toBeInTheDocument();
  });

  it('shares one explanation request with the request card', async () => {
    vi.mocked(api.getExplanation).mockResolvedValue(makeExplanation());
    useAppStore.setState({ selectedRequestId: '50104', whyOpen: true });
    render(
      <>
        <ExplanationCard />
        <WhyPanel />
      </>,
    );
    expect(await within(panel()).findByText('Проверки')).toBeInTheDocument();
    expect(await screen.findByText(/Назначена Бригада Арташкин/)).toBeInTheDocument();
    expect(api.getExplanation).toHaveBeenCalledTimes(1);
  });

  it('shows the error of the explanation request', async () => {
    vi.mocked(api.getExplanation).mockRejectedValue(new api.ApiError(404, 'Заявка не найдена'));
    useAppStore.setState({ selectedRequestId: '50104', whyOpen: true });
    render(<WhyPanel />);
    expect(await within(panel()).findByText('Заявка не найдена')).toBeInTheDocument();
  });
});

describe('panel «Почему» in the store', () => {
  it('opens only with something open on the right and closes when nothing is left there', () => {
    act(() => useAppStore.getState().toggleWhy());
    expect(useAppStore.getState().whyOpen).toBe(false);

    act(() => useAppStore.getState().selectEngineer('E01'));
    act(() => useAppStore.getState().toggleWhy());
    expect(useAppStore.getState().whyOpen).toBe(true);

    // Карточка заявки поверх бригады и возврат к бригаде панель не закрывают.
    act(() => useAppStore.getState().selectRequest('50104'));
    act(() => useAppStore.getState().selectRequest(null));
    expect(useAppStore.getState().whyOpen).toBe(true);

    act(() => useAppStore.getState().selectEngineer(null));
    expect(useAppStore.getState().whyOpen).toBe(false);
    // Следующий выбор не открывает панель сам.
    act(() => useAppStore.getState().selectRequest('50104'));
    expect(useAppStore.getState().whyOpen).toBe(false);
  });

  it('closes when the open request disappears from a new plan and no brigade is open', () => {
    useAppStore.setState({ selectedRequestId: '50104', whyOpen: true });
    const state = makePlanningState();
    act(() => useAppStore.getState().setPlanningState({ ...state, requests: state.requests.filter((request) => request.id !== '50104') }));
    expect(useAppStore.getState()).toMatchObject({ selectedRequestId: null, whyOpen: false });
  });

  it('is closed after «Другой файл»', () => {
    useAppStore.setState({ selectedEngineerId: 'E01', whyOpen: true });
    act(() => useAppStore.getState().reset());
    expect(useAppStore.getState().whyOpen).toBe(false);
  });
});
