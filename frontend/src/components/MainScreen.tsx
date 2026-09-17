import { useAppStore } from '../store/useAppStore';
import { DiffBanner } from './DiffBanner';
import { ErrorToast } from './ErrorToast';
import { ChoiceDialog } from './events/ChoiceDialog';
import { EngineerDelayDialog } from './events/EngineerDelayDialog';
import { EngineerUnavailableDialog } from './events/EngineerUnavailableDialog';
import { EventToolbar } from './events/EventToolbar';
import { RequestEditDialog } from './events/RequestEditDialog';
import { TransportChangeDialog } from './events/TransportChangeDialog';
import { MapView } from './map/MapView';
import { MetricsStrip } from './MetricsStrip';
import { RightPanel } from './panel/RightPanel';
import { TimeBar } from './TimeBar';
import { WhyPanel } from './WhyPanel';

export function MainScreen() {
  const pickMode = useAppStore((s) => s.pickMode);
  const pickFor = useAppStore((s) => s.pickFor);
  const editingRequestId = useAppStore((s) => s.editingRequestId);
  const delayDialogOpen = useAppStore((s) => s.delayDialogOpen);
  const engineerDialog = useAppStore((s) => s.engineerDialog?.kind ?? null);
  // Срочная заявка и плавающий диалог открыты вместе: плавающий стоит левее, чтобы не закрыть срочную заявку.
  const toolbarDialogOpen = useAppStore((s) => s.toolbarDialog !== null);
  // Панель «Почему» встаёт слева отдельной колонкой и сдвигает карту, а не закрывает маршрут.
  // Колонка есть, только когда панели есть что показать: пустая колонка сдвинула бы карту и правую панель.
  const whyOpen = useAppStore((s) => s.whyOpen && (s.selectedRequestId !== null || s.selectedEngineerId !== null));
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
      <TimeBar />
      <DiffBanner />
      <div className={`workspace${whyOpen ? ' workspace--why' : ''}`}>
        <WhyPanel />
        <section className={`map-area${pickMode ? ' map-area--picking' : ''}`} aria-label="Карта">
          {pickMode && <div className="map-hint">{pickHint}</div>}
          <MapView />
        </section>
        <RightPanel />
      </div>
      {/* Плавающие диалоги открываются на одном месте; стор держит открытым только один из них. */}
      {editingRequestId && <RequestEditDialog />}
      {delayDialogOpen && <EngineerDelayDialog />}
      {engineerDialog === 'transport' && <TransportChangeDialog />}
      {engineerDialog === 'unavailable' && <EngineerUnavailableDialog />}
      <ChoiceDialog />
      <ErrorToast />
    </div>
  );
}
