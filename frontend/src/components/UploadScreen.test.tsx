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

  it('shows the backend error', () => {
    resetStore({ error: 'В файле нет колонок: Адрес' });
    render(<UploadScreen />);
    expect(screen.getByRole('alert')).toHaveTextContent('В файле нет колонок: Адрес');
  });
});
