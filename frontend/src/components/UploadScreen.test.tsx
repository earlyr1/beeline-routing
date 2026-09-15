import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, uploadFile: vi.fn(), getDatasetStatus: vi.fn(), buildPlan: vi.fn() };
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
    render(<UploadScreen />);

    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['x'], 'east.csv')] } });

    expect(await screen.findByRole('heading', { name: 'Восток' })).toBeInTheDocument();
    expect(screen.getByText('Файл: east.csv')).toBeInTheDocument();
    expect(screen.getByText('Не найдены на карте: 1')).toBeInTheDocument();
    expect(screen.getByText('Пропущено строк: 2')).toBeInTheDocument();
    expect(screen.getByText('Дорожный граф OSRM')).toBeInTheDocument();

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

  it('shows the backend error', () => {
    resetStore({ error: 'В файле нет колонок: Адрес' });
    render(<UploadScreen />);
    expect(screen.getByRole('alert')).toHaveTextContent('В файле нет колонок: Адрес');
  });
});
