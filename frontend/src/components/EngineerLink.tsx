import { useAppStore } from '../store/useAppStore';

/** Имя инженера ссылкой на страницу бригады. Клик не доходит до строки, внутри которой стоит ссылка. */
export function EngineerLink({ engineerId, name }: { engineerId: string; name: string }) {
  const selectRequest = useAppStore((s) => s.selectRequest);
  const selectEngineer = useAppStore((s) => s.selectEngineer);
  return (
    <button
      type="button"
      className="link-button"
      title="Открыть страницу бригады"
      onClick={(event) => {
        event.stopPropagation();
        selectRequest(null);
        selectEngineer(engineerId);
      }}
    >
      {name}
    </button>
  );
}
