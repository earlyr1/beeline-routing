import { useEffect, useRef } from 'react';
import './fallbackMap.css';
import { MapOverviewButton } from './MapOverviewButton';
import { useMapLocation } from './useMapLocation';
import { activateMarker, pickPoint, useMapModel } from './useMapModel';
import type { LngLat, YMapsComponents } from './yandexLoader';

export const SETTLE_REFRESH_MS = 400;
export const RESIZE_REFRESH_MS = 150;

export function MapContent({ components }: { components: YMapsComponents }) {
  const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapMarker, YMapFeature, YMapListener } = components;
  const model = useMapModel();
  const { location, showWholePlan, refreshLocation } = useMapLocation();
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const settle = window.setTimeout(refreshLocation, SETTLE_REFRESH_MS);
    const host = hostRef.current;
    if (!host || typeof ResizeObserver === 'undefined') return () => window.clearTimeout(settle);
    let debounce: number | undefined;
    const observer = new ResizeObserver(() => {
      window.clearTimeout(debounce);
      debounce = window.setTimeout(refreshLocation, RESIZE_REFRESH_MS);
    });
    observer.observe(host);
    return () => {
      window.clearTimeout(settle);
      window.clearTimeout(debounce);
      observer.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!model) return null;

  return (
    <>
      <MapOverviewButton onClick={showWholePlan} />
      <div ref={hostRef} className="ymap-host">
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
      </div>
    </>
  );
}
