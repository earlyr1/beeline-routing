import { useEffect, type SyntheticEvent } from 'react';
import { useAppStore } from '../../store/useAppStore';

/** Меню карты рисуется выше маркеров заявок, в том числе выбранной. */
export const MAP_MENU_Z_INDEX = 1000;

/** Клики внутри меню не доходят до карты: иначе карта открыла бы меню в новой точке или начала перетаскивание. */
const keepInside = (event: SyntheticEvent) => event.stopPropagation();

/** Меню после клика по пустому месту карты: добавить срочную заявку в этой точке. Одно для Яндекс Карт и OpenStreetMap. */
export function MapMenu() {
  const point = useAppStore((s) => s.mapMenu);
  const locked = useAppStore((s) => s.busy || s.showPrevious);
  const addRequestAt = useAppStore((s) => s.addRequestAt);
  const closeMapMenu = useAppStore((s) => s.closeMapMenu);

  useEffect(() => {
    if (!point) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeMapMenu();
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [point, closeMapMenu]);

  if (!point) return null;
  return (
    <div
      className="map-menu"
      role="group"
      aria-label="Меню карты"
      onClick={keepInside}
      onDoubleClick={keepInside}
      onMouseDown={keepInside}
      onPointerDown={keepInside}
    >
      <button type="button" className="btn btn-small btn-primary" disabled={locked} onClick={() => void addRequestAt(point)}>
        Добавить заявку здесь
      </button>
      <button type="button" className="btn btn-ghost btn-small" aria-label="Закрыть меню карты" onClick={closeMapMenu}>
        ✕
      </button>
    </div>
  );
}
