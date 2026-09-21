import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, uploadFile: vi.fn(), getDatasetStatus: vi.fn(), buildPlan: vi.fn(), moveCursor: vi.fn() };
});

import * as api from '../api/client';
import { useAppStore } from '../store/useAppStore';
import { makeDatasetStatus, makePlanningState } from '../test/fixtures';
import { resetStore } from '../test/store';
import { UploadScreen } from './UploadScreen';

beforeEach(() => {
  vi.resetAllMocks();
  resetStore();
});

describe('UploadScreen', () => {
  it('uploads a chosen file, shows the report and builds the plan', async () => {
    vi.mocked(api.uploadFile).mockResolvedValue(makeDatasetStatus());
    vi.mocked(api.buildPlan).mockResolvedValue(makePlanningState());
    // После расчёта часы встают на начало дня, и это время уходит на сервер.
    vi.mocked(api.moveCursor).mockResolvedValue(makePlanningState({ cursor: '09:00' }));
    render(<UploadScreen />);

    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['x'], 'east.csv')] } });

    expect(await screen.findByRole('heading', { name: 'Восток' })).toBeInTheDocument();
    expect(screen.getByText('Файл: east.csv')).toBeInTheDocument();
    expect(screen.getByText('Не найдены на карте: 1')).toBeInTheDocument();
    expect(screen.getByText('Пропущено строк: 2')).toBeInTheDocument();
    expect(screen.getByText('Дорожный граф OSRM')).toBeInTheDocument();
    // Чистые окна — чистый отчёт: пустого раздела в нём не появляется.
    expect(screen.queryByText(/Замечания к окнам/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Спланировать' }));
    await waitFor(() => expect(useAppStore.getState().state?.version).toBe(4));
  });

  it('shows the processing stage and progress without the plan button', () => {
    resetStore({
      datasetStatus: makeDatasetStatus({ status: 'processing', stage: 'geocoding', progress: { done: 12, total: 66 }, report: null }),
    });
    render(<UploadScreen />);
    expect(screen.getByText('Поиск адресов на карте')).toBeInTheDocument();
    expect(screen.getByText('12 из 66')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Спланировать' })).not.toBeInTheDocument();
  });

  it('lets the dispatcher set the workload level and the lunch before the upload and plans the day with them', async () => {
    vi.mocked(api.uploadFile).mockResolvedValue(makeDatasetStatus());
    vi.mocked(api.buildPlan).mockResolvedValue(makePlanningState({ workload_level: 0, lunch_enabled: false }));
    vi.mocked(api.moveCursor).mockResolvedValue(makePlanningState({ workload_level: 0, lunch_enabled: false, cursor: '09:00' }));
    render(<UploadScreen />);

    const slider = screen.getByRole('slider', { name: 'Нагрузка инженеров' });
    expect(screen.getByRole('heading', { name: 'Нагрузка инженеров' })).toBeInTheDocument();
    expect(slider).toHaveValue('1');
    expect(slider).toHaveAttribute('min', '0');
    expect(slider).toHaveAttribute('max', '2');
    expect(slider).toHaveAttribute('step', '1');
    expect(screen.getByText('😌')).toBeInTheDocument();
    expect(screen.getByText('🥵')).toBeInTheDocument();
    expect(screen.getByText('😐 Обычный день: Баланс между числом инженеров и пробегом')).toBeInTheDocument();
    expect(screen.getByText('Запас на дорогу: +10%')).toBeInTheDocument();

    fireEvent.change(slider, { target: { value: '2' } });
    expect(useAppStore.getState().workloadLevel).toBe(2);
    expect(screen.getByText('🥵 На пределе: Меньше инженеров, каждому больше заявок')).toBeInTheDocument();
    expect(screen.getByText('Запас на дорогу: без запаса')).toBeInTheDocument();

    fireEvent.change(slider, { target: { value: '0' } });
    expect(screen.getByText('😌 Спокойный день: Больше инженеров, больше запас на дорогу')).toBeInTheDocument();
    expect(screen.getByText('Запас на дорогу: +30%')).toBeInTheDocument();

    const lunch = screen.getByRole('checkbox', { name: 'Обед по плану' });
    expect(lunch).toBeChecked();
    expect(
      screen.getByText('45 минут в середине смены у каждого инженера. С обедом план считается до 30 секунд, без обеда до 5.'),
    ).toHaveClass('muted');
    fireEvent.click(lunch);
    expect(lunch).not.toBeChecked();
    expect(useAppStore.getState().lunchEnabled).toBe(false);

    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['x'], 'east.csv')] } });
    expect(await screen.findByRole('heading', { name: 'Восток' })).toBeInTheDocument();
    const planButton = screen.getByRole('button', { name: 'Спланировать' });
    const sliderAfterUpload = screen.getByRole('slider', { name: 'Нагрузка инженеров' });
    expect(sliderAfterUpload).toHaveValue('0');
    expect(screen.getByRole('checkbox', { name: 'Обед по плану' })).not.toBeChecked();
    expect(sliderAfterUpload.compareDocumentPosition(planButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(
      screen.getByRole('checkbox', { name: 'Обед по плану' }).compareDocumentPosition(planButton) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    fireEvent.click(planButton);
    await waitFor(() => expect(api.buildPlan).toHaveBeenCalledWith('d_test', { workload_level: 0, lunch: false }));
  });

  it('blocks the workload slider and the lunch only while the plan is being built', () => {
    resetStore({ busy: true, datasetStatus: makeDatasetStatus({ status: 'processing', stage: 'geocoding', report: null }) });
    const { unmount } = render(<UploadScreen />);
    expect(screen.getByRole('slider', { name: 'Нагрузка инженеров' })).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: 'Обед по плану' })).toBeEnabled();
    unmount();

    resetStore({ busy: true, datasetStatus: makeDatasetStatus() });
    render(<UploadScreen />);
    expect(screen.getByRole('button', { name: 'Считаем план…' })).toBeDisabled();
    expect(screen.getByRole('slider', { name: 'Нагрузка инженеров' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Обед по плану' })).toBeDisabled();
  });

  it('shows what is wrong with the windows from the file and says that those requests stayed in the day', () => {
    const windowWarnings = [
      'строка 5: у заявки N4 окно 02:00–04:00 вне рабочего дня 10:00–22:00 — приехать в него некому',
      'строка 6: у заявки N5 окно приезда 10:00–10:10 — всего 10 мин, бригада должна попасть ровно в них, а работ по типу «Подключение» на 70 мин',
      'окна не по сетке: 3 из 5 (в том числе названные выше), например строка 7 — заявка N6, 11:30–13:30. Слоты сетки: 10:00–12:00, 12:00–14:00, 14:00–16:00, 16:00–18:00, 18:00–20:00, 20:00–22:00',
    ];
    const status = makeDatasetStatus();
    resetStore({ datasetStatus: { ...status, report: { ...status.report!, window_warnings: windowWarnings } } });
    render(<UploadScreen />);

    expect(screen.getByText('Замечания к окнам: 3')).toBeInTheDocument();
    for (const warning of windowWarnings) {
      expect(screen.getByText(warning)).toBeInTheDocument();
    }
    // Диспетчер должен видеть, что эти заявки остались в дне, — в отличие от пропущенных строк.
    expect(
      screen.getByText(/Эти заявки остались в дне со своими окнами/),
    ).toHaveClass('muted');
    // Выпавшая строка важнее оставшейся с замечанием, поэтому «Пропущено строк» стоит выше.
    const summaries = screen.getAllByText(/Пропущено строк: 2|Замечания к окнам: 3/).map((node) => node.textContent);
    expect(summaries).toEqual(['Пропущено строк: 2', 'Замечания к окнам: 3']);
  });

  it('shows the backend error', () => {
    resetStore({ error: 'В файле нет колонок: Адрес' });
    render(<UploadScreen />);
    expect(screen.getByRole('alert')).toHaveTextContent('В файле нет колонок: Адрес');
  });
});
