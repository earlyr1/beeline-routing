import { useAppStore } from '../store/useAppStore';

export function ErrorToast() {
  const error = useAppStore((s) => s.error);
  const clearError = useAppStore((s) => s.clearError);
  if (!error) return null;
  return (
    <div className="toast" role="alert">
      <span>{error}</span>
      <button type="button" className="btn btn-ghost btn-small" onClick={clearError} aria-label="Закрыть сообщение">
        ✕
      </button>
    </div>
  );
}
