import * as L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useEffect, useRef, useState } from 'react';
import type { MapMarker } from '../../lib/mapModel';
import { toLatLng, toLatLngBounds } from '../../lib/mapView';
import './fallbackMap.css';
import { MapOverviewButton } from './MapOverviewButton';
import { useMapLocation } from './useMapLocation';
import { activateMarker, pickPoint, useMapModel } from './useMapModel';
import type { MapLocation } from './yandexLoader';

export const OSM_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const OSM_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">участники OpenStreetMap</a>';
/** Leaflet складывает zIndexOffset с экранной координатой y, поэтому шаг больше любой высоты карты. */
const Z_INDEX_STEP = 10_000;

interface Scene {
  map: L.Map;
  routes: L.LayerGroup;
  markers: L.LayerGroup;
}

function markerElement(marker: MapMarker): HTMLElement {
  const element = document.createElement('div');
  element.className = marker.className;
  element.title = marker.title;
  element.textContent = marker.label;
  Object.assign(element.style, marker.style);
  return element;
}

function applyLocation(map: L.Map, location: MapLocation, animate: boolean): void {
  const options = { animate: animate && location.duration !== undefined, duration: (location.duration ?? 0) / 1000 };
  if ('bounds' in location) map.fitBounds(toLatLngBounds(location.bounds), options);
  else map.setView(toLatLng(location.center), location.zoom, options);
}

/** Карта на Leaflet и OpenStreetMap: то же содержимое, что у Яндекс Карт, но без ключа API. */
export function FallbackMap({ note, detail }: { note: string; detail?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scene, setScene] = useState<Scene | null>(null);
  const placedMap = useRef<L.Map | null>(null);
  const model = useMapModel();
  const { location, showWholePlan } = useMapLocation();

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const map = L.map(container, { zoomControl: false, attributionControl: false });
    L.control.zoom({ zoomInTitle: 'Приблизить', zoomOutTitle: 'Отдалить' }).addTo(map);
    L.control.attribution({ prefix: false }).addTo(map);
    L.tileLayer(OSM_TILE_URL, { attribution: OSM_ATTRIBUTION, maxZoom: 19 }).addTo(map);
    map.on('click', (event: L.LeafletMouseEvent) => pickPoint([event.latlng.lng, event.latlng.lat]));
    // Высота карты меняется без resize окна, например когда появляется баннер изменений.
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => map.invalidateSize());
    observer?.observe(container);
    setScene({ map, routes: L.layerGroup().addTo(map), markers: L.layerGroup().addTo(map) });
    return () => {
      observer?.disconnect();
      map.remove();
      setScene(null);
    };
  }, []);

  useEffect(() => {
    if (!scene) return;
    applyLocation(scene.map, location, placedMap.current === scene.map);
    placedMap.current = scene.map;
  }, [scene, location]);

  useEffect(() => {
    if (!scene) return;
    scene.routes.clearLayers();
    scene.markers.clearLayers();
    if (!model) return;
    for (const line of model.polylines) {
      L.polyline(line.coordinates.map(toLatLng), {
        color: line.color,
        opacity: line.opacity,
        weight: line.width,
        interactive: false,
      }).addTo(scene.routes);
    }
    for (const marker of model.markers) {
      const clickable = marker.target.kind !== 'office';
      const layer = L.marker(toLatLng(marker.coordinates), {
        icon: L.divIcon({ html: markerElement(marker), className: 'fallback-map__icon', iconSize: undefined }),
        zIndexOffset: marker.zIndex * Z_INDEX_STEP,
        interactive: clickable,
        keyboard: false,
      });
      if (clickable) layer.on('click', () => activateMarker(marker.target));
      layer.addTo(scene.markers);
    }
  }, [scene, model]);

  return (
    <>
      {model && <MapOverviewButton onClick={showWholePlan} />}
      <div className="fallback-map">
        <div ref={containerRef} className="fallback-map__canvas" data-testid="fallback-map" />
        <div className="fallback-map__note" role="note" title={detail}>
          {note}
        </div>
      </div>
    </>
  );
}
