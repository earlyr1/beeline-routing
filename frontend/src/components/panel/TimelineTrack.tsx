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

/** Окно заявки поверх дорожек: начало и конец, % шкалы, и подпись для подсказки. */
export interface WindowMarks {
  from: number;
  to: number;
  label: string;
}

/** Две серые вертикальные линии окна: у каждой дорожки свои, вместе они идут через весь таймлайн. */
export function WindowMarkLines({ marks }: { marks: WindowMarks }) {
  const title = `Окно заявки ${marks.label}`;
  return (
    <>
      <div className="timeline__mark" style={{ left: `${marks.from}%` }} title={title} />
      <div className="timeline__mark" style={{ left: `${marks.to}%` }} title={title} />
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
  /** Окно выбранной заявки без исполнителя: серые линии показывают, чем заняты бригады в это время. */
  marks?: WindowMarks | null;
}

/** Дорожка одного инженера: смена, обед, окна визитов, работы, недоступность и текущее время. Общая для таймлайна и страницы бригады. */
export function TimelineTrack({ row, color, nowLeft, selectedRequestId, onSelect, marks }: TimelineTrackProps) {
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
            title={`${bar.requestLabel}: ${bar.label}${bar.clipped ? ' (выходит за шкалу)' : ''}${bar.window ? `, ${bar.window}` : ''}`}
            aria-label={`Заявка ${bar.requestLabel} ${bar.label}`}
            onClick={() => onSelect(bar.requestId)}
          />
        </Fragment>
      ))}
      {marks && <WindowMarkLines marks={marks} />}
      {nowLeft > 0 && <div className="timeline__now" style={{ left: `${nowLeft}%` }} />}
    </div>
  );
}
