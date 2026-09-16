import { useCallback, useMemo } from 'react';
import { isClickableMarker, type MapMarker, type MapPolyline } from '../../lib/mapModel';
import { useAppStore } from '../../store/useAppStore';
import { MAP_MENU_Z_INDEX, MapMenu } from './MapMenu';
import { MapOverviewButton } from './MapOverviewButton';
import { useMapLocation } from './useMapLocation';
import { activateMarker, pickPoint, useMapLayers } from './useMapModel';
import type { LngLat, YMapsComponents } from './yandexLoader';

export function MapContent({ components }: { components: YMapsComponents }) {
  const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapMarker, YMapFeature, YMapListener } = components;
  const layers = useMapLayers();
  const { location, showWholePlan } = useMapLocation();
  const mapMenu = useAppStore((s) => s.mapMenu);
  const model = layers?.model ?? null;

  const renderLine = useCallback(
    (line: MapPolyline) => (
      <YMapFeature
        key={line.key}
        geometry={{ type: 'LineString', coordinates: line.coordinates }}
        style={{ stroke: [{ color: line.stroke, width: line.width }] }}
      />
    ),
    [YMapFeature],
  );

  const renderMarker = useCallback(
    (marker: MapMarker) => (
      <YMapMarker key={marker.key} coordinates={marker.coordinates} zIndex={marker.zIndex}>
        <div
          className={marker.className}
          style={marker.style}
          title={marker.title}
          onClick={
            isClickableMarker(marker)
              ? (event) => {
                  event.stopPropagation();
                  activateMarker(marker.target);
                }
              : undefined
          }
        >
          {marker.label}
        </div>
      </YMapMarker>
    ),
    [YMapMarker],
  );

  // Слой плана считается отдельно: на шаге часов React его не перерисовывает, двигается только слой часов.
  const planLayer = useMemo(
    () =>
      model && (
        <>
          {model.polylines.map(renderLine)}
          {model.markers.map(renderMarker)}
        </>
      ),
    [model, renderLine, renderMarker],
  );

  if (!model) return null;

  return (
    <>
      <MapOverviewButton onClick={showWholePlan} />
      <YMap location={location} mode="vector">
        <YMapDefaultSchemeLayer />
        <YMapDefaultFeaturesLayer />
        {planLayer}
        {layers?.clock?.polylines.map(renderLine)}
        {layers?.clock?.markers.map(renderMarker)}
        {mapMenu && (
          <YMapMarker key="map-menu" coordinates={[mapMenu.lon, mapMenu.lat]} zIndex={MAP_MENU_Z_INDEX}>
            <MapMenu />
          </YMapMarker>
        )}
        {/* Объект под курсором приходит первым аргументом: клик по маркеру или по меню карты меню не открывает. */}
        <YMapListener
          layer="any"
          onClick={(object: unknown, event: { coordinates: LngLat }) => pickPoint(event.coordinates, object === undefined)}
        />
      </YMap>
    </>
  );
}
