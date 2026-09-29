import { engineerColor } from '../../lib/colors';
import { requestLabel, toMinutes } from '../../lib/format';
import { engineerIdsOf } from '../../lib/planView';
import { dayScale, percent, timelineRows } from '../../lib/timeline';
import { useAppStore } from '../../store/useAppStore';
import { EngineerLink } from '../EngineerLink';
import { TimelineTicks, TimelineTrack, WindowMarkLines, type WindowMarks } from './TimelineTrack';

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
  // Выбрана заявка без исполнителя: её окно — серыми линиями через все дорожки, чтобы было видно, чем заняты
  // бригады в это время и почему никто её не взял.
  const selected = selectedRequestId ? state.requests.find((request) => request.id === selectedRequestId) : undefined;
  const marks: WindowMarks | null =
    selected && selected.status !== 'cancelled' && state.plan.unassigned.some((item) => item.request_id === selected.id)
      ? {
          from: percent(scale, toMinutes(selected.window_start)),
          to: percent(scale, toMinutes(selected.window_end)),
          label: `${selected.window_start}–${selected.window_end}`,
        }
      : null;

  return (
    <div className="timeline">
      <div className="timeline__row timeline__row--header">
        <div className="timeline__label" />
        <div className="timeline__track">
          <TimelineTicks scale={scale} />
          {marks && <WindowMarkLines marks={marks} />}
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
            <TimelineTrack row={row} color={color} nowLeft={nowLeft} selectedRequestId={selectedRequestId} onSelect={selectRequest} marks={marks} />
          </div>
        );
      })}
      <p className="muted timeline__legend">
        Полоса: работа по плану. Рамка: окно визита. Штриховка: работа уже началась или инженер недоступен. Красная линия:
        текущее время {clock}.
        {marks && selected && ` Серые линии: окно ${marks.label} заявки ${requestLabel(selected.id, selected.priority)} без исполнителя.`}
      </p>
    </div>
  );
}
