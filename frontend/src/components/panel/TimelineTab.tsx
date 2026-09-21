import { engineerColor } from '../../lib/colors';
import { toMinutes } from '../../lib/format';
import { engineerIdsOf } from '../../lib/planView';
import { dayScale, percent, timelineRows } from '../../lib/timeline';
import { useAppStore } from '../../store/useAppStore';
import { EngineerLink } from '../EngineerLink';
import { TimelineTicks, TimelineTrack } from './TimelineTrack';

export function TimelineTab() {
  const state = useAppStore((s) => s.state);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const clock = useAppStore((s) => s.clock);
  if (!state) return null;

  // Та же шкала дня, что у часов: линия текущего времени идёт вместе с ползунком.
  const scale = dayScale(state);
  const rows = timelineRows(state, state.plan, scale);
  const ids = engineerIdsOf(state);
  const nowLeft = percent(scale, toMinutes(clock));

  return (
    <div className="timeline">
      <div className="timeline__row timeline__row--header">
        <div className="timeline__label" />
        <div className="timeline__track">
          <TimelineTicks scale={scale} />
        </div>
      </div>
      {rows.map((row) => {
        const color = engineerColor(row.engineer.id, ids);
        return (
          <div key={row.engineer.id} className="timeline__row">
            <div className="timeline__label">
              <span className="dot" style={{ background: color }} />
              <EngineerLink engineerId={row.engineer.id} name={row.engineer.name} />
            </div>
            <TimelineTrack row={row} color={color} nowLeft={nowLeft} selectedRequestId={selectedRequestId} onSelect={selectRequest} />
          </div>
        );
      })}
      <p className="muted timeline__legend">
        Полоса: работа по плану. Рамка: окно визита. Штриховка: работа уже началась или инженер недоступен. Красная линия:
        текущее время {clock}.
      </p>
    </div>
  );
}
