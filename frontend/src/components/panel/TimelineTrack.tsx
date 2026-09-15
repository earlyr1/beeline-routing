import { Fragment } from 'react';
import { fromMinutes } from '../../lib/format';
import { hourTicks, percent, type TimelineRow, type TimeScale } from '../../lib/timeline';

/** Подписи часов над дорожками таймлайна. */
export function TimelineTicks({ scale }: { scale: TimeScale }) {
  return (
    <>
      {hourTicks(scale).map((tick) => (
        <span key={tick} className="timeline__tick" style={{ left: `${percent(scale, tick)}%` }}>
          {fromMinutes(tick)}
        </span>
      ))}
    </>
  );
}

interface TimelineTrackProps {
  row: TimelineRow;
  color: string;
  /** Линия текущего времени, % шкалы; 0 — линия не рисуется. */
  nowLeft: number;
  selectedRequestId: string | null;
  onSelect(requestId: string): void;
}

/** Дорожка одного инженера: смена, обед, окна визитов, работы, недоступность и текущее время. Общая для таймлайна и страницы бригады. */
export function TimelineTrack({ row, color, nowLeft, selectedRequestId, onSelect }: TimelineTrackProps) {
  return (
    <div className="timeline__track">
      <div className="timeline__shift" style={{ left: `${row.shiftLeft}%`, width: `${row.shiftWidth}%` }} />
      {row.unavailableLeft !== null && (
        <div className="timeline__unavailable" style={{ left: `${row.unavailableLeft}%` }} title="Инженер недоступен" />
      )}
      {row.lunch && (
        <div className="timeline__lunch" style={{ left: `${row.lunch.left}%`, width: `${row.lunch.width}%` }} title={`Обед ${row.lunch.label}`}>
          Обед
        </div>
      )}
      {row.bars.map((bar) => (
        <Fragment key={bar.requestId}>
          <div className="timeline__window" style={{ left: `${bar.windowLeft}%`, width: `${bar.windowWidth}%`, borderColor: color }} />
          <button
            type="button"
            className={[
              'timeline__bar',
              bar.pinned ? 'timeline__bar--pinned' : '',
              bar.urgent ? 'timeline__bar--urgent' : '',
              bar.late ? 'timeline__bar--late' : '',
              bar.clipped ? 'timeline__bar--clipped' : '',
              bar.requestId === selectedRequestId ? 'timeline__bar--selected' : '',
            ]
              .filter(Boolean)
              .join(' ')}
            style={{ left: `${bar.left}%`, width: `${bar.width}%`, background: color }}
            title={`${bar.requestId}: ${bar.label}${bar.clipped ? ' (выходит за шкалу)' : ''}${bar.window ? `, ${bar.window}` : ''}`}
            aria-label={`Заявка ${bar.requestId} ${bar.label}`}
            onClick={() => onSelect(bar.requestId)}
          />
        </Fragment>
      ))}
      {nowLeft > 0 && <div className="timeline__now" style={{ left: `${nowLeft}%` }} />}
    </div>
  );
}
