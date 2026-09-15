import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, getExplanation: vi.fn() };
});

import * as api from '../../api/client';
import { makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { RightPanel } from './RightPanel';

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getExplanation).mockReturnValue(new Promise(() => undefined));
  resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedEngineerId: 'E01' });
});

describe('RightPanel', () => {
  it('shows the route card above the tabs when an engineer is selected', () => {
    render(<RightPanel />);
    const card = screen.getByRole('region', { name: 'Бригада' });
    const position = card.compareDocumentPosition(screen.getByRole('tablist'));
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('gives way to the request explanation while a request card is open', () => {
    resetStore({ datasetId: 'd_test', state: makePlanningState(), selectedEngineerId: 'E01', selectedRequestId: '50104' });
    render(<RightPanel />);
    expect(screen.getByRole('region', { name: 'Объяснение по заявке' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Бригада' })).not.toBeInTheDocument();
  });
});
