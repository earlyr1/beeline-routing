import { useAppStore } from '../store/useAppStore';

/** Имя инженера ссылкой на страницу бригады. Клик не доходит до строки, внутри которой стоит ссылка. */
export function EngineerLink({ engineerId, name }: { engineerId: string; name: string }) {
  const openBrigade = useAppStore((s) => s.openBrigade);
  return (
    <button
      type="button"
      className="link-button"
      title="Открыть страницу бригады"
      onClick={(event) => {
        event.stopPropagation();
        openBrigade(engineerId);
      }}
    >
      {name}
    </button>
  );
}
