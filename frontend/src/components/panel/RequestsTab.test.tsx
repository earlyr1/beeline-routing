import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { makeAsapRequest, makeAsapState, makeDataUrgentState, makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { RequestsTab } from './RequestsTab';

const rowOf = (requestId: string) => screen.getByText(requestId).closest('li') as HTMLElement;
const titles = () => screen.getAllByRole('listitem').map((item) => item.querySelector('strong')?.textContent);
const filter = () => screen.getByRole('button', { name: /^Без исполнителя/ });

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:30' });
});

describe('RequestsTab', () => {
  it('lists requests with engineer, planned start and badges', () => {
    render(<RequestsTab />);
    const moved = rowOf('50104');
    expect(within(moved).getByText('Начало 14:00')).toBeInTheDocument();
    expect(within(moved).getByText('Бригада Арташкин')).toBeInTheDocument();
    expect(within(moved).getByText('Перенесена от «Бригада Белузин»')).toHaveAttribute(
      'title',
      'Перенос от «Бригада Белузин» к «Бригада Арташкин»',
    );

    const urgent = rowOf('URG-001');
    expect(within(urgent).getByText('Срочная')).toBeInTheDocument();
    expect(within(urgent).getByText('Новое назначение')).toBeInTheDocument();

    expect(within(rowOf('18754')).getByText('Не назначена')).toBeInTheDocument();
    // Отменённая заявка без кнопок отмены и возврата: передумать можно только в уведомлении сразу после отмены.
    expect(within(rowOf('10135')).queryByRole('button', { name: 'Вернуть' })).not.toBeInTheDocument();
    expect(within(rowOf('10135')).queryByRole('button', { name: 'Отменить' })).not.toBeInTheDocument();
  });

  it('метит подключение уровнем распределения, а ремонт и аварию не метит', () => {
    render(<RequestsTab />);
    // Средний уровень виден меткой; нижний — это большинство заявок, у них метки нет.
    const connection = within(rowOf('74198')).getByText('Подключение');
    expect(connection).toHaveClass('badge');
    expect(connection).toHaveAttribute('title', 'Приоритет распределения: авария → подключение → ремонт и дозаказ');
    expect(within(rowOf('50104')).queryByText('Подключение')).not.toBeInTheDocument();
    // Верхний уровень и так виден меткой «Срочная» и приставкой URG-.
    expect(within(rowOf('URG-001')).queryByText('Авария')).not.toBeInTheDocument();
  });

  it('shows an urgent request of the day as URG-… and keeps the raw number in the event and in the store', () => {
    const cancelRequest = vi.fn();
    resetStore({ datasetId: 'd_test', state: makeDataUrgentState(), clock: '13:30' });
    useAppStore.setState({ cancelRequest });
    render(<RequestsTab />);
    const urgent = rowOf('URG-50104');
    expect(within(urgent).getByText('Срочная')).toBeInTheDocument();
    // Заявка диспетчера приходит уже с приставкой, обычная заявка остаётся со своим номером.
    expect(rowOf('URG-001')).toBeInTheDocument();
    expect(rowOf('46393')).toBeInTheDocument();
    expect(screen.queryByText('URG-URG-001')).not.toBeInTheDocument();

    fireEvent.click(within(urgent).getByRole('button', { name: 'Отменить' }));
    expect(cancelRequest).toHaveBeenCalledWith({ type: 'cancel', time: '13:30', request: null, request_id: '50104', engineer_id: null });
    fireEvent.click(urgent);
    expect(useAppStore.getState().selectedRequestId).toBe('50104');
  });

  it('cancels a request at the chosen event time without selecting the row', () => {
    const cancelRequest = vi.fn();
    useAppStore.setState({ cancelRequest });
    render(<RequestsTab />);
    fireEvent.click(within(rowOf('50104')).getByRole('button', { name: 'Отменить' }));
    expect(cancelRequest).toHaveBeenCalledWith({ type: 'cancel', time: '13:30', request: null, request_id: '50104', engineer_id: null });
    expect(useAppStore.getState().selectedRequestId).toBeNull();
  });

  it('opens the brigade page from the engineer of a row without selecting the row', () => {
    useAppStore.setState({ selectedRequestId: '46393' });
    render(<RequestsTab />);
    fireEvent.click(within(rowOf('URG-001')).getByRole('button', { name: 'Бригада Белузин' }));
    expect(useAppStore.getState()).toMatchObject({ selectedEngineerId: 'E02', selectedRequestId: null });
  });

  it('selects a request on row click', () => {
    render(<RequestsTab />);
    fireEvent.click(rowOf('46393'));
    expect(useAppStore.getState().selectedRequestId).toBe('46393');
  });

  it('filters by engineer in route order and blocks cancelling started work', () => {
    render(<RequestsTab />);
    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E01' } });
    expect(titles()).toEqual(['74198', '86160', '50104', '46393']);
    expect(within(rowOf('74198')).getByRole('button', { name: 'Отменить' })).toBeDisabled();
    expect(useAppStore.getState().selectedEngineerId).toBe('E01');
  });

  it('opens the request edit from a row without selecting it', () => {
    render(<RequestsTab />);
    fireEvent.click(within(rowOf('50104')).getByRole('button', { name: 'Изменить' }));
    expect(useAppStore.getState().editingRequestId).toBe('50104');
    expect(useAppStore.getState().selectedRequestId).toBeNull();
  });

  it('blocks editing started work exactly like cancelling and allows editing a cancelled request', () => {
    render(<RequestsTab />);
    const started = within(rowOf('74198')).getByRole('button', { name: 'Изменить' });
    expect(started).toBeDisabled();
    expect(started).toHaveAttribute('title', 'Работа уже началась, изменить нельзя');
    expect(within(rowOf('74198')).getByRole('button', { name: 'Отменить' })).toBeDisabled();
    expect(within(rowOf('50104')).getByRole('button', { name: 'Изменить' })).not.toHaveAttribute('title');
    expect(within(rowOf('10135')).getByRole('button', { name: 'Изменить' })).toBeEnabled();
  });

  it('shows «как можно скорее» with the start of waiting instead of the window and a badge next to «Срочная»', () => {
    resetStore({ datasetId: 'd_test', state: makeAsapState(), clock: '13:30' });
    render(<RequestsTab />);
    const asap = rowOf('URG-002');
    expect(within(asap).getByText('Как можно скорее с 13:00')).toBeInTheDocument();
    expect(within(asap).queryByText(/^Окно/)).not.toBeInTheDocument();
    expect(Array.from(asap.querySelectorAll('.badge')).map((badge) => badge.textContent)).toEqual(['Срочная', 'Как можно скорее']);

    const windowed = rowOf('URG-001');
    expect(within(windowed).getByText('Окно 13:00–15:00')).toBeInTheDocument();
    expect(within(windowed).queryByText('Как можно скорее')).not.toBeInTheDocument();
  });

  it('помечает заявки, к которым бригада везёт оборудование', () => {
    render(<RequestsTab />);
    const badge = within(rowOf('74198')).getByText('Оборудование');
    expect(badge).toHaveClass('badge', 'badge--equipment');
    expect(badge).toHaveAttribute('title', 'Нужно привезти оборудование: роутер, приставка или колонка');
    expect(within(rowOf('86160')).getByText('Оборудование')).toBeInTheDocument();
    expect(within(rowOf('50104')).queryByText('Оборудование')).not.toBeInTheDocument();
    expect(within(rowOf('18754')).queryByText('Оборудование')).not.toBeInTheDocument();
  });

  it('показывает, что с заявкой происходит на время часов', () => {
    render(<RequestsTab />);
    // Часы на 13:30: к 50104 инженер выехал в 13:00 и приедет в 13:35, у URG-001 работы идут с 13:05.
    expect(within(rowOf('50104')).getByText('В пути')).toHaveClass('badge', 'badge--clock-driving');
    expect(within(rowOf('URG-001')).getByText('В работе')).toHaveClass('badge--clock-working');
    expect(within(rowOf('74198')).getByText('Выполнена')).toHaveClass('badge--clock-done');
    expect(within(rowOf('46393')).queryByText(/^(Выполнена|В работе|В пути)$/)).not.toBeInTheDocument();
    expect(within(rowOf('18754')).queryByText(/^(Выполнена|В работе|В пути)$/)).not.toBeInTheDocument();
  });

  it('двигает статусы заявок вместе с часами', () => {
    render(<RequestsTab />);
    act(() => useAppStore.getState().setClock('14:10'));
    expect(within(rowOf('50104')).getByText('В работе')).toBeInTheDocument();
    expect(within(rowOf('50104')).queryByText('В пути')).not.toBeInTheDocument();
  });

  it('показывает у заявки без бригады причину и вид работ', () => {
    render(<RequestsTab />);
    const row = rowOf('18754');
    expect(within(row).getByText('Не назначена')).toBeInTheDocument();
    expect(within(row).getByText('Не помещается в окно или смену')).toHaveClass('badge', 'badge--warn');
    expect(
      within(row).getByText(
        'Работа не помещается в окно 18:00–20:00 или в смену: даже без других заявок Бригада Белузин начнёт не раньше 20:10.',
      ),
    ).toHaveClass('request-row__reason');
    // Бригады у заявки нет: на её месте вид работ, по нему видно, кто мог бы её взять.
    expect(within(row).getByText('Работы на подключение и дозаказы')).toBeInTheDocument();
    expect(within(rowOf('50104')).queryByText('Локальные работы')).not.toBeInTheDocument();
    expect(rowOf('50104').querySelector('.request-row__reason')).toBeNull();
  });

  it('фильтр «Без исполнителя» оставляет только заявки без бригады, их число — на самой кнопке', () => {
    render(<RequestsTab />);
    expect(filter()).toHaveAttribute('aria-pressed', 'false');
    expect(within(filter()).getByText('1')).toHaveClass('requests-tab__count');

    fireEvent.click(filter());
    expect(filter()).toHaveAttribute('aria-pressed', 'true');
    expect(useAppStore.getState().unassignedOnly).toBe(true);
    expect(titles()).toEqual(['18754']);
    expect(screen.getByText('Заявок: 1')).toBeInTheDocument();
    expect(within(rowOf('18754')).getByText(/даже без других заявок Бригада Белузин/)).toBeInTheDocument();
    fireEvent.click(rowOf('18754'));
    expect(useAppStore.getState().selectedRequestId).toBe('18754');

    fireEvent.click(filter());
    expect(titles()).toHaveLength(makePlanningState().requests.length);
  });

  it('в фильтре срочная заявка подписана URG-…, а у «как можно скорее» вместо окна начало ожидания', () => {
    const state = makeDataUrgentState(['18754']);
    const asap = { request_id: 'URG-002', reason_code: 'no_free_engineer_in_window' as const, reason_text: 'Сегодня никто не успевает.' };
    resetStore({
      datasetId: 'd_test',
      state: { ...state, requests: [...state.requests, makeAsapRequest()], plan: { ...state.plan, unassigned: [...state.plan.unassigned, asap] } },
      clock: '13:30',
      unassignedOnly: true,
    });
    render(<RequestsTab />);
    expect(within(filter()).getByText('2')).toBeInTheDocument();
    expect(titles()).toEqual(['URG-002', 'URG-18754']);
    const waiting = rowOf('URG-002');
    expect(within(waiting).getByText('Как можно скорее с 13:00')).toBeInTheDocument();
    expect(within(waiting).getByText('Аварийные работы')).toBeInTheDocument();
    expect(within(waiting).getByText('Сегодня никто не успевает.')).toBeInTheDocument();
    expect(within(rowOf('URG-18754')).getByText('Окно 18:00–20:00')).toBeInTheDocument();
    fireEvent.click(rowOf('URG-18754'));
    expect(useAppStore.getState().selectedRequestId).toBe('18754');
  });

  it('в фильтре без заявок без бригады пишет, что все распределены, и оставляет кнопку, чтобы снять фильтр', () => {
    const state = makePlanningState();
    resetStore({ datasetId: 'd_test', state: { ...state, plan: { ...state.plan, unassigned: [] } }, unassignedOnly: true });
    render(<RequestsTab />);
    expect(screen.getByText('Все заявки распределены.')).toBeInTheDocument();
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
    expect(filter()).toHaveTextContent(/^Без исполнителя$/);
    expect(filter()).toHaveAttribute('aria-pressed', 'true');
  });

  it('фильтр оставляет страницу бригады открытой и показывает под ней заявки без бригады, пока его не снимут', () => {
    useAppStore.setState({ selectedEngineerId: 'E01' });
    render(<RequestsTab />);
    expect(titles()).toEqual(['74198', '86160', '50104', '46393']);
    fireEvent.click(filter());
    expect(useAppStore.getState().selectedEngineerId).toBe('E01');
    expect(filter()).toHaveAttribute('aria-pressed', 'true');
    // Список показывает не маршрут бригады, поэтому в списке инженеров «Все инженеры».
    expect(screen.getByLabelText('Инженер')).toHaveValue('');
    expect(titles()).toEqual(['18754']);

    // Бригада, открытая поверх фильтра, его не снимает.
    act(() => useAppStore.getState().openBrigade('E02'));
    expect(filter()).toHaveAttribute('aria-pressed', 'true');
    expect(titles()).toEqual(['18754']);

    // Снятый фильтр возвращает маршрут открытой бригады.
    fireEvent.click(filter());
    expect(screen.getByLabelText('Инженер')).toHaveValue('E02');
    expect(titles()).toEqual(['84627', 'URG-001']);
  });

  it('выбор в списке инженеров снимает фильтр: это явный выбор, чей маршрут показать', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), clock: '13:30', unassignedOnly: true });
    render(<RequestsTab />);
    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: 'E01' } });
    expect(filter()).toHaveAttribute('aria-pressed', 'false');
    expect(useAppStore.getState().selectedEngineerId).toBe('E01');
    expect(titles()).toEqual(['74198', '86160', '50104', '46393']);

    // После закрытия бригады фильтр не возвращается.
    fireEvent.change(screen.getByLabelText('Инженер'), { target: { value: '' } });
    expect(useAppStore.getState().selectedEngineerId).toBeNull();
    expect(filter()).toHaveAttribute('aria-pressed', 'false');
    expect(titles()).toHaveLength(makePlanningState().requests.length);
  });

  it('disables editing while an event is being applied', () => {
    useAppStore.setState({ busy: true });
    render(<RequestsTab />);
    expect(within(rowOf('50104')).getByRole('button', { name: 'Изменить' })).toBeDisabled();
  });
});
