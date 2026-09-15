import { initialAppData, useAppStore, type AppState } from '../store/useAppStore';

const pristine = useAppStore.getState();

/** Полный сброс стора между тестами, включая подменённые действия. */
export function resetStore(patch: Partial<AppState> = {}): void {
  useAppStore.setState({ ...pristine, ...initialAppData, ...patch }, true);
}
