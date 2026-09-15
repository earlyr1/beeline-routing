import { Fragment } from 'react';
import { engineerColor } from '../../lib/colors';
import { fromMinutes, toMinutes } from '../../lib/format';
import { displayedPlan, engineerIdsOf } from '../../lib/planView';
import { hourTicks, percent, timelineRows, timeScale } from '../../lib/timeline';
import { useAppStore } from '../../store/useAppStore';

export function TimelineTab() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectRequest = useAppStore((s) => s.selectRequest);
  if (!state) return null;

  const plan = displayedPlan(state, showPrevious);
  const scale = timeScale(state, plan);
  const rows = timelineRows(state, plan, scale);
  const ids = engineerIdsOf(state);
  const nowLeft = percent(scale, toMinutes(state.now));

  return (
    <div className="timeline">
      <div className="timeline__row timeline__row--header">
        <div className="timeline__label" />
        <div className="timeline__track">
          {hourTicks(scale).map((tick) => (
            <span key={tick} className="timeline__tick" style={{ left: `${percent(scale, tick)}%` }}>
              {fromMinutes(tick)}
            </span>
          ))}
        </div>
      </div>
      {rows.map((row) => {
        const color = engineerColor(row.engineer.id, ids);
        return (
          <div key={row.engineer.id} className="timeline__row">
            <div className="timeline__label">
              <span className="dot" style={{ background: color }} />
              {row.engineer.name}
            </div>
            <div className="timeline__track">
              <div className="timeline__shift" style={{ left: `${row.shiftLeft}%`, width: `${row.shiftWidth}%` }} />
              {row.unavailableLeft !== null && (
                <div className="timeline__unavailable" style={{ left: `${row.unavailableLeft}%` }} title="Инженер недоступен" />
              )}
              {row.bars.map((bar) => (
                <Fragment key={bar.requestId}>
                  <div
                    className="timeline__window"
                    style={{ left: `${bar.windowLeft}%`, width: `${bar.windowWidth}%`, borderColor: color }}
                  />
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
                    title={`${bar.requestId}: ${bar.label}${bar.clipped ? ' (выходит за шкалу)' : ''}`}
                    aria-label={`Заявка ${bar.requestId} ${bar.label}`}
                    onClick={() => selectRequest(bar.requestId)}
                  />
                </Fragment>
              ))}
              {nowLeft > 0 && <div className="timeline__now" style={{ left: `${nowLeft}%` }} />}
            </div>
          </div>
        );
      })}
      <p className="muted timeline__legend">
        Полоса: работа по плану. Рамка: окно визита. Штриховка: работа уже началась или инженер недоступен. Красная линия:
        текущее время {state.now}.
      </p>
    </div>
  );
}
