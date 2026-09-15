import { useEffect, useState } from 'react';
import { useAppStore } from '../../store/useAppStore';
import { FallbackMap } from './FallbackMap';
import { MapContent } from './MapContent';
import { loadYandexMaps, type YMapsComponents } from './yandexLoader';

export const NO_KEY_NOTE = 'Подложка OpenStreetMap: ключ Яндекс Карт не задан';
export const YANDEX_FAILED_NOTE = 'Яндекс Карты недоступны, показана подложка OpenStreetMap';

export function MapPlaceholder({ title, text }: { title: string; text?: string }) {
  return (
    <div className="map-placeholder">
      <h3>{title}</h3>
      {text && <p>{text}</p>}
    </div>
  );
}

function YandexMap({ apiKey }: { apiKey: string }) {
  const [components, setComponents] = useState<YMapsComponents | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    loadYandexMaps(apiKey)
      .then((loaded) => {
        if (alive) setComponents(loaded);
      })
      .catch((error: unknown) => {
        if (alive) setLoadError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      alive = false;
    };
  }, [apiKey]);

  // Лимит ключа исчерпан или нет сети: план всё равно виден на OpenStreetMap.
  if (loadError) return <FallbackMap note={YANDEX_FAILED_NOTE} detail={loadError} />;
  if (!components) return <MapPlaceholder title="Загружаем Яндекс Карты…" />;
  return <MapContent components={components} />;
}

export function MapView() {
  const config = useAppStore((s) => s.config);
  if (!config) return <MapPlaceholder title="Загружаем настройки карты…" />;
  if (!config.yandex_maps_api_key) return <FallbackMap note={NO_KEY_NOTE} />;
  return <YandexMap apiKey={config.yandex_maps_api_key} />;
}
