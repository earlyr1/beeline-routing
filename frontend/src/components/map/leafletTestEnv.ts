import { afterAll, beforeAll } from 'vitest';

export const MAP_WIDTH = 800;
export const MAP_HEIGHT = 600;

/**
 * Только для тестов. В jsdom у элементов нет размеров, и Leaflet считает контейнер карты
 * пустым: fitBounds уходит на нулевой масштаб, а клик нельзя перевести в координаты.
 */
export function stubMapContainerSize(): void {
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => MAP_WIDTH });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => MAP_HEIGHT });
  });
  afterAll(() => {
    Reflect.deleteProperty(HTMLElement.prototype, 'clientWidth');
    Reflect.deleteProperty(HTMLElement.prototype, 'clientHeight');
  });
}
