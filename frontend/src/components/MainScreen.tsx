import { useAppStore } from '../store/useAppStore';
import { DiffBanner } from './DiffBanner';
import { ErrorToast } from './ErrorToast';
import { EventToolbar } from './events/EventToolbar';
import { MapView } from './map/MapView';
import { MetricsStrip } from './MetricsStrip';
import { RightPanel } from './panel/RightPanel';

export function MainScreen() {
  const pickMode = useAppStore((s) => s.pickMode);
  return (
    <div className="app-shell">
      <header className="topbar">
        <MetricsStrip />
        <EventToolbar />
      </header>
      <DiffBanner />
      <div className="workspace">
        <section className={`map-area${pickMode ? ' map-area--picking' : ''}`} aria-label="Карта">
          {pickMode && <div className="map-hint">Кликните по карте, чтобы указать место срочной заявки</div>}
          <MapView />
        </section>
        <RightPanel />
      </div>
      <ErrorToast />
    </div>
  );
}
