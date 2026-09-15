import * as React from 'react';
import * as ReactDOM from 'react-dom';
import type { ComponentType, ReactNode } from 'react';

/** [lon, lat] */
export type LngLat = [number, number];

export interface MapLocation {
  center: LngLat;
  zoom: number;
  duration?: number;
}

export interface LineStyle {
  stroke: { color: string; width: number }[];
}

/** Узкий интерфейс компонентов Яндекс Карт, которыми пользуется приложение. */
export interface YMapsComponents {
  YMap: ComponentType<{ location: MapLocation; mode?: 'vector' | 'raster'; children?: ReactNode }>;
  YMapDefaultSchemeLayer: ComponentType;
  YMapDefaultFeaturesLayer: ComponentType;
  YMapMarker: ComponentType<{ coordinates: LngLat; zIndex?: number; children?: ReactNode }>;
  YMapFeature: ComponentType<{ geometry: { type: 'LineString'; coordinates: LngLat[] }; style?: LineStyle }>;
  YMapListener: ComponentType<{ layer?: string; onClick?: (object: unknown, event: { coordinates: LngLat }) => void }>;
}

let pending: Promise<YMapsComponents> | null = null;

function injectScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if ('ymaps3' in window) {
      resolve();
      return;
    }
    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Не удалось загрузить Яндекс Карты. Проверьте интернет и ключ API.'));
    document.head.appendChild(script);
  });
}

export function loadYandexMaps(apiKey: string): Promise<YMapsComponents> {
  if (!pending) {
    pending = (async () => {
      await injectScript(`https://api-maps.yandex.ru/v3/?apikey=${encodeURIComponent(apiKey)}&lang=ru_RU`);
      const [ymaps3React] = await Promise.all([ymaps3.import('@yandex/ymaps3-reactify'), ymaps3.ready]);
      const reactify = ymaps3React.reactify.bindTo(React, ReactDOM);
      const module = reactify.module(ymaps3);
      return {
        YMap: module.YMap,
        YMapDefaultSchemeLayer: module.YMapDefaultSchemeLayer,
        YMapDefaultFeaturesLayer: module.YMapDefaultFeaturesLayer,
        YMapMarker: module.YMapMarker,
        YMapFeature: module.YMapFeature,
        YMapListener: module.YMapListener,
      } as unknown as YMapsComponents;
    })();
    pending.catch(() => {
      pending = null;
    });
  }
  return pending;
}
