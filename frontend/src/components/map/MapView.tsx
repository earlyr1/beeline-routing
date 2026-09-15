import { useEffect, useState } from 'react';
import { useAppStore } from '../../store/useAppStore';
import { MapContent } from './MapContent';
import { loadYandexMaps, type YMapsComponents } from './yandexLoader';

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

  if (loadError) return <MapPlaceholder title="Карта не загрузилась" text={loadError} />;
  if (!components) return <MapPlaceholder title="Загружаем Яндекс Карты…" />;
  return <MapContent components={components} />;
}

export function MapView() {
  const config = useAppStore((s) => s.config);
  if (!config) return <MapPlaceholder title="Загружаем настройки карты…" />;
  if (!config.yandex_maps_api_key) {
    return (
      <MapPlaceholder
        title="Карта отключена"
        text="Ключ Яндекс Карт не задан. Добавьте YANDEX_MAPS_API_KEY в .env и перезапустите backend. Список заявок, таймлайн и перепланирование работают без карты."
      />
    );
  }
  return <YandexMap apiKey={config.yandex_maps_api_key} />;
}
