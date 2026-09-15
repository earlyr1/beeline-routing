import { MapOverviewButton } from './MapOverviewButton';
import { useMapLocation } from './useMapLocation';
import { activateMarker, pickPoint, useMapModel } from './useMapModel';
import type { LngLat, YMapsComponents } from './yandexLoader';

export function MapContent({ components }: { components: YMapsComponents }) {
  const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapMarker, YMapFeature, YMapListener } = components;
  const model = useMapModel();
  const { location, showWholePlan } = useMapLocation();

  if (!model) return null;

  return (
    <>
      <MapOverviewButton onClick={showWholePlan} />
      <YMap location={location} mode="vector">
        <YMapDefaultSchemeLayer />
        <YMapDefaultFeaturesLayer />
        {model.polylines.map((line) => (
          <YMapFeature
            key={line.key}
            geometry={{ type: 'LineString', coordinates: line.coordinates }}
            style={{ stroke: [{ color: line.stroke, width: line.width }] }}
          />
        ))}
        {model.markers.map((marker) => (
          <YMapMarker key={marker.key} coordinates={marker.coordinates} zIndex={marker.zIndex}>
            <div
              className={marker.className}
              style={marker.style}
              title={marker.title}
              onClick={
                marker.target.kind === 'office'
                  ? undefined
                  : (event) => {
                      event.stopPropagation();
                      activateMarker(marker.target);
                    }
              }
            >
              {marker.label}
            </div>
          </YMapMarker>
        ))}
        <YMapListener layer="any" onClick={(_object: unknown, event: { coordinates: LngLat }) => pickPoint(event.coordinates)} />
      </YMap>
    </>
  );
}
