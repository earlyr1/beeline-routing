import { clearExplanationCache } from '../components/useExplanation';
import { initialAppData, resetAppSession, useAppStore, type AppState } from '../store/useAppStore';

const pristine = useAppStore.getState();

/**
 * Полный сброс стора между тестами, включая подменённые действия, таймеры проигрывания и очередь запросов.
 * Часы стоят на времени переданного плана, как после настоящего ответа сервера, если тест не задал их сам.
 */
export function resetStore(patch: Partial<AppState> = {}): void {
  resetAppSession();
  clearExplanationCache();
  const clock = patch.state?.cursor ?? initialAppData.clock;
  useAppStore.setState({ ...pristine, ...initialAppData, clock, ...patch }, true);
}
