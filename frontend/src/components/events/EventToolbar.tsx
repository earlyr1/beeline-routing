import { useAppStore } from '../../store/useAppStore';
import { UrgentRequestDialog } from './UrgentRequestDialog';

/**
 * Панель событий: срочная заявка. Время события берётся с часов дня, события инженера открываются
 * со страницы бригады и из меню «Добавить событие» на часах.
 */
export function EventToolbar() {
  const state = useAppStore((s) => s.state);
  const busy = useAppStore((s) => s.busy);
  const showPrevious = useAppStore((s) => s.showPrevious);
  // Открытый диалог хранит стор: главный экран сдвигает по нему плавающие диалоги, а меню карты и часы открывают его сами.
  const urgentOpen = useAppStore((s) => s.toolbarDialog === 'urgent');
  const openDialog = useAppStore((s) => s.openToolbarDialog);
  const closeDialog = useAppStore((s) => s.closeToolbarDialog);
  if (!state) return null;

  return (
    <div className="event-toolbar">
      <button type="button" className="btn btn-danger" disabled={busy || showPrevious} onClick={() => openDialog('urgent')}>
        Срочная заявка
      </button>
      {urgentOpen && <UrgentRequestDialog onClose={closeDialog} />}
    </div>
  );
}
