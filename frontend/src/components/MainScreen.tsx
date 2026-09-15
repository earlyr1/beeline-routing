import { useAppStore } from '../store/useAppStore';
import { DiffBanner } from './DiffBanner';
import { ErrorToast } from './ErrorToast';
import { EngineerDelayDialog } from './events/EngineerDelayDialog';
import { EventToolbar } from './events/EventToolbar';
import { RequestEditDialog } from './events/RequestEditDialog';
import { MapView } from './map/MapView';
import { MetricsStrip } from './MetricsStrip';
import { RightPanel } from './panel/RightPanel';

export function MainScreen() {
  const pickMode = useAppStore((s) => s.pickMode);
  const pickFor = useAppStore((s) => s.pickFor);
  const editingRequestId = useAppStore((s) => s.editingRequestId);
  const delayDialogOpen = useAppStore((s) => s.delayDialogOpen);
  // Диалог панели событий и диалог изменения заявки или задержки открыты вместе: второй стоит левее, чтобы не закрыть первый.
  const toolbarDialogOpen = useAppStore((s) => s.toolbarDialog !== null);
  // Подсказка называет диалог, который начал выбор точки: оба диалога могут быть открыты одновременно.
  const pickHint =
    pickFor === 'edit' && editingRequestId
      ? `Кликните по карте, чтобы указать новое место заявки ${editingRequestId}`
      : pickFor === 'urgent'
        ? 'Кликните по карте, чтобы указать место срочной заявки'
        : 'Кликните по карте, чтобы указать точку';
  return (
    <div className={`app-shell${toolbarDialogOpen ? ' app-shell--toolbar-dialog' : ''}`}>
      <header className="topbar">
        <MetricsStrip />
        <EventToolbar />
      </header>
      <DiffBanner />
      <div className="workspace">
        <section className={`map-area${pickMode ? ' map-area--picking' : ''}`} aria-label="Карта">
          {pickMode && <div className="map-hint">{pickHint}</div>}
          <MapView />
        </section>
        <RightPanel />
      </div>
      {editingRequestId && <RequestEditDialog />}
      {delayDialogOpen && <EngineerDelayDialog />}
      <ErrorToast />
    </div>
  );
}
