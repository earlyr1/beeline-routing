import { useEffect, useRef, useState } from 'react';
import { engineerColor } from '../../lib/colors';
import { shortAddress } from '../../lib/format';
import { overviewBounds } from '../../lib/mapView';
import { assignmentIndex, diffMarks, displayedPlan, engineerIdsOf, type DiffMark } from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';
import { useRouteGeometries } from './useRouteGeometries';
import type { LngLat, MapLocation, YMapsComponents } from './yandexLoader';

const MOSCOW_CENTER: LngLat = [37.6176, 55.7558];

export function MapContent({ components }: { components: YMapsComponents }) {
  const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapMarker, YMapFeature, YMapListener } = components;
  const state = useAppStore((s) => s.state);
  const datasetId = useAppStore((s) => s.datasetId);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const selectEngineer = useAppStore((s) => s.selectEngineer);
  const pickMode = useAppStore((s) => s.pickMode);
  const finishPick = useAppStore((s) => s.finishPick);
  const plan = state ? displayedPlan(state, showPrevious) : null;
  const legs = useRouteGeometries(datasetId, state, plan, showPrevious ? 'previous' : 'current');
  const [location, setLocation] = useState<MapLocation>(() =>
    state ? { bounds: overviewBounds(state) } : { center: MOSCOW_CENTER, zoom: 10 },
  );
  const firstSelection = useRef(true);

  const showOverview = () => {
    const current = useAppStore.getState().state;
    if (current) setLocation({ bounds: overviewBounds(current), duration: 400 });
  };

  useEffect(() => {
    if (firstSelection.current) {
      firstSelection.current = false;
      if (!selectedRequestId) return;
    }
    if (!selectedRequestId) {
      showOverview();
      return;
    }
    const request = useAppStore.getState().state?.requests.find((item) => item.id === selectedRequestId);
    if (request && request.lat !== null && request.lon !== null) {
      setLocation({ center: [request.lon, request.lat], zoom: 14, duration: 400 });
    }
  }, [selectedRequestId]);

  if (!state || !plan) return null;
  const ids = engineerIdsOf(state);
  const assignments = assignmentIndex(plan);
  const marks: Map<string, DiffMark> = showPrevious ? new Map() : diffMarks(state.last_diff);

  const onMapClick = (_object: unknown, event: { coordinates: LngLat }) => {
    if (useAppStore.getState().pickMode) finishPick({ lon: event.coordinates[0], lat: event.coordinates[1] });
  };

  return (
    <>
      <button
        type="button"
        className="btn btn-small map-overview-button"
        onClick={() => {
          if (useAppStore.getState().selectedRequestId) selectRequest(null);
          else showOverview();
        }}
      >
        Весь план
      </button>
      <YMap location={location} mode="vector">
        <YMapDefaultSchemeLayer />
        <YMapDefaultFeaturesLayer />
        {plan.routes.flatMap((route) => {
          const color = engineerColor(route.engineer_id, ids);
          const dimmed = selectedEngineerId !== null && selectedEngineerId !== route.engineer_id;
          const width = selectedEngineerId === route.engineer_id ? 6 : 3;
          return (legs.get(route.engineer_id) ?? []).map((leg, index) => (
            <YMapFeature
              key={`${route.engineer_id}-${index}-${leg.to_request_id}`}
              geometry={{ type: 'LineString', coordinates: leg.coordinates }}
              style={{ stroke: [{ color: dimmed ? `${color}40` : color, width }] }}
            />
          ));
        })}
        <YMapMarker coordinates={[state.office.lon, state.office.lat]} zIndex={20}>
          <div className="marker marker--office" title={state.office.address}>
            Офис
          </div>
        </YMapMarker>
        {state.engineers.map((engineer) => {
          const color = engineerColor(engineer.id, ids);
          const dimmed = !engineer.available || (selectedEngineerId !== null && selectedEngineerId !== engineer.id);
          return (
            <YMapMarker key={`start-${engineer.id}`} coordinates={[engineer.start_lon, engineer.start_lat]} zIndex={30}>
              <div
                className={`marker marker--start${dimmed ? ' marker--dimmed' : ''}`}
                style={{ borderColor: color, color }}
                title={`Старт: ${engineer.name}`}
                onClick={(event) => {
                  event.stopPropagation();
                  if (!pickMode) selectEngineer(selectedEngineerId === engineer.id ? null : engineer.id);
                }}
              >
                ⌂
              </div>
            </YMapMarker>
          );
        })}
        {state.requests.map((request) => {
          if (request.lat === null || request.lon === null) return null;
          const info = assignments.get(request.id);
          const cancelled = request.status === 'cancelled';
          const classes = [
            'marker',
            info ? '' : 'marker--unassigned',
            request.priority === 'urgent' ? 'marker--urgent' : '',
            cancelled ? 'marker--cancelled' : '',
            marks.has(request.id) ? 'marker--changed' : '',
            request.id === selectedRequestId ? 'marker--selected' : '',
            selectedEngineerId && info?.engineerId !== selectedEngineerId ? 'marker--dimmed' : '',
          ]
            .filter(Boolean)
            .join(' ');
          return (
            <YMapMarker key={request.id} coordinates={[request.lon, request.lat]} zIndex={request.id === selectedRequestId ? 100 : 10}>
              <div
                className={classes}
                style={{ background: cancelled ? '#ffffff' : engineerColor(info?.engineerId, ids) }}
                title={`${request.id}: ${shortAddress(request.address)}`}
                onClick={(event) => {
                  event.stopPropagation();
                  if (!pickMode) selectRequest(request.id);
                }}
              >
                {cancelled ? '×' : info ? info.order + 1 : '!'}
              </div>
            </YMapMarker>
          );
        })}
        <YMapListener layer="any" onClick={onMapClick} />
      </YMap>
    </>
  );
}
