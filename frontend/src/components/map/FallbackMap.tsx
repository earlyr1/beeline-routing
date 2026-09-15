import * as L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { MapMarker, MapPolyline } from '../../lib/mapModel';
import { toLatLng, toLatLngBounds } from '../../lib/mapView';
import { useAppStore } from '../../store/useAppStore';
import './fallbackMap.css';
import { MAP_MENU_Z_INDEX, MapMenu } from './MapMenu';
import { MapOverviewButton } from './MapOverviewButton';
import { useMapLocation } from './useMapLocation';
import { activateMarker, pickPoint, useMapLayers } from './useMapModel';
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
  /** Слой часов дня: маркеры «где сейчас» и текущий отрезок пути. */
  now: L.LayerGroup;
}

function markerElement(marker: MapMarker): HTMLElement {
  const element = document.createElement('div');
  element.className = marker.className;
  element.title = marker.title;
  element.textContent = marker.label;
  Object.assign(element.style, marker.style);
  return element;
}

function addMarker(group: L.LayerGroup, marker: MapMarker): void {
  const clickable = marker.target.kind !== 'office';
  const layer = L.marker(toLatLng(marker.coordinates), {
    icon: L.divIcon({ html: markerElement(marker), className: 'fallback-map__icon', iconSize: undefined }),
    zIndexOffset: marker.zIndex * Z_INDEX_STEP,
    interactive: clickable,
    keyboard: false,
  });
  if (clickable) layer.on('click', () => activateMarker(marker.target));
  layer.addTo(group);
}

function addLine(group: L.LayerGroup, line: MapPolyline): void {
  L.polyline(line.coordinates.map(toLatLng), {
    color: line.color,
    opacity: line.opacity,
    weight: line.width,
    interactive: false,
  }).addTo(group);
}

/** Узел для меню карты: React рисует в него меню, Leaflet ставит его в маркер и не считает клики внутри кликами по карте. */
function mapMenuContainer(): HTMLElement {
  const element = document.createElement('div');
  L.DomEvent.disableClickPropagation(element);
  L.DomEvent.disableScrollPropagation(element);
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
  const layers = useMapLayers();
  const model = layers?.model ?? null;
  const clock = layers?.clock ?? null;
  const { location, showWholePlan } = useMapLocation();
  const mapMenu = useAppStore((s) => s.mapMenu);
  const [menuElement] = useState(mapMenuContainer);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const map = L.map(container, { zoomControl: false, attributionControl: false });
    L.control.zoom({ zoomInTitle: 'Приблизить', zoomOutTitle: 'Отдалить' }).addTo(map);
    L.control.attribution({ prefix: false }).addTo(map);
    L.tileLayer(OSM_TILE_URL, { attribution: OSM_ATTRIBUTION, maxZoom: 19 }).addTo(map);
    // Клик по маркеру до карты не доходит: у маркеров Leaflet клики не всплывают.
    map.on('click', (event: L.LeafletMouseEvent) => pickPoint([event.latlng.lng, event.latlng.lat]));
    // Высота карты меняется без resize окна, например когда появляется баннер изменений.
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => map.invalidateSize());
    observer?.observe(container);
    setScene({
      map,
      routes: L.layerGroup().addTo(map),
      markers: L.layerGroup().addTo(map),
      now: L.layerGroup().addTo(map),
    });
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
    for (const line of model.polylines) addLine(scene.routes, line);
    for (const marker of model.markers) addMarker(scene.markers, marker);
  }, [scene, model]);

  // Слой часов свой: на каждом шаге часов пересобираются только маркеры «где сейчас» и текущий отрезок пути.
  useEffect(() => {
    if (!scene) return;
    scene.now.clearLayers();
    if (!clock) return;
    for (const line of clock.polylines) addLine(scene.now, line);
    for (const marker of clock.markers) addMarker(scene.now, marker);
  }, [scene, clock]);

  useEffect(() => {
    if (!scene || !mapMenu) return;
    const layer = L.marker(toLatLng([mapMenu.lon, mapMenu.lat]), {
      icon: L.divIcon({ html: menuElement, className: 'fallback-map__icon', iconSize: undefined }),
      zIndexOffset: MAP_MENU_Z_INDEX * Z_INDEX_STEP,
      interactive: false,
      keyboard: false,
    }).addTo(scene.map);
    return () => {
      layer.remove();
    };
  }, [scene, mapMenu, menuElement]);

  return (
    <>
      {model && <MapOverviewButton onClick={showWholePlan} />}
      <div className="fallback-map">
        <div ref={containerRef} className="fallback-map__canvas" data-testid="fallback-map" />
        <div className="fallback-map__note" role="note" title={detail}>
          {note}
        </div>
      </div>
      {createPortal(<MapMenu />, menuElement)}
    </>
  );
}
