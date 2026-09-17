import { engineerColor } from '../lib/colors';
import { describeEvent } from '../lib/events';
import {
  formatDuration,
  formatKm,
  formatWindow,
  plural,
  requestWindowText,
  SKILL_LABELS,
  toMinutes,
  TRANSPORT_LABELS,
} from '../lib/format';
import { byId, displayedPlan, engineerIdsOf, routeRows, routeSummary, type RouteStop } from '../lib/planView';
import { timelineStatusText } from '../lib/timeBar';
import { dayScale, percent, timelineRow } from '../lib/timeline';
import { useAppStore } from '../store/useAppStore';
import { TimelineTicks, TimelineTrack } from './panel/TimelineTrack';
import { WhyButton } from './WhyPanel';

function slackText(stop: RouteStop): string {
  if (stop.slackMin === null) return '—';
  return stop.slackMin < 0 ? `опоздание ${-stop.slackMin} мин` : `${stop.slackMin} мин`;
}

/** Сколько единиц оборудования брать с собой; без оборудования в итогах об этом ничего нет. */
function equipmentText(count: number): string {
  return count === 0 ? '' : ` · оборудование ${count} ${plural(count, 'единица', 'единицы', 'единиц')}`;
}

/**
 * Страница бригады выбранного инженера: профиль и действия, личный таймлайн, итоги с кнопкой «Почему?»,
 * визиты по порядку и применённые события бригады. Разбор маршрута открывается в панели «Почему».
 */
export function RouteCard() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const selectedEngineerId = useAppStore((s) => s.selectedEngineerId);
  const selectedRequestId = useAppStore((s) => s.selectedRequestId);
  const selectEngineer = useAppStore((s) => s.selectEngineer);
  const selectRequest = useAppStore((s) => s.selectRequest);
  const busy = useAppStore((s) => s.busy);
  const startDelay = useAppStore((s) => s.startDelay);
  const openEngineerDialog = useAppStore((s) => s.openEngineerDialog);
  const clock = useAppStore((s) => s.clock);
  if (!state || !selectedEngineerId || selectedRequestId) return null;
  const plan = displayedPlan(state, showPrevious);
  const summary = routeSummary(state, plan, selectedEngineerId);
  if (!summary) return null;

  const { engineer, stops } = summary;
  const equipment = equipmentText(summary.equipmentCount);
  const availability = engineer.available ? null : engineer.unavailable_from ? `недоступен с ${engineer.unavailable_from}` : 'недоступен';
  const profile = [
    engineer.skills.map((skill) => SKILL_LABELS[skill]).join(', '),
    TRANSPORT_LABELS[engineer.transport],
    `смена ${formatWindow(engineer.shift_start, engineer.shift_end)}`,
    availability,
  ]
    .filter(Boolean)
    .join(' · ');
  // События меняют текущий план, поэтому в плане до события и во время расчёта кнопки недоступны.
  const locked = busy || showPrevious;
  // Недоступному инженеру сервер не принимает ни задержку, ни смену транспорта, ни повторную недоступность.
  const unavailable = !engineer.available;
  // Та же шкала дня, что у часов: линия текущего времени стоит там же, где ползунок.
  const scale = dayScale(state, plan);
  const row = timelineRow(state, plan, scale, engineer);
  // События шкалы дня в порядке применения, а показываем с последнего: применённые, впереди и отклонённые.
  const events = (state.timeline ?? []).filter((item) => item.event.engineer_id === engineer.id).reverse();
  const engineers = byId(state.engineers);

  return (
    <section className="explanation route-card" aria-label="Бригада">
      <header className="explanation__head">
        <div>
          <h3>{engineer.name}</h3>
          <p className="muted">{profile}</p>
        </div>
        <div className="explanation__actions">
          <button
            type="button"
            className="btn btn-small"
            disabled={locked || unavailable}
            title={unavailable ? 'Инженер недоступен, сменить транспорт нельзя' : undefined}
            onClick={() => openEngineerDialog('transport', engineer.id)}
          >
            Смена транспорта
          </button>
          <button
            type="button"
            className="btn btn-small"
            disabled={locked || unavailable}
            title={unavailable ? 'Инженер недоступен, задержку поставить нельзя' : undefined}
            onClick={() => startDelay(engineer.id)}
          >
            Задержка
          </button>
          <button
            type="button"
            className="btn btn-small"
            disabled={locked || unavailable}
            title={unavailable ? `Инженер уже недоступен${engineer.unavailable_from ? ` с ${engineer.unavailable_from}` : ''}` : undefined}
            onClick={() => openEngineerDialog('unavailable', engineer.id)}
          >
            Недоступен
          </button>
          <button type="button" className="btn btn-ghost btn-small" onClick={() => selectEngineer(null)} aria-label="Закрыть бригаду">
            ✕
          </button>
        </div>
      </header>
      {showPrevious && state.previous_plan && <p className="note">Маршрут по плану до события.</p>}
      <div className="timeline timeline--personal" role="group" aria-label="Таймлайн бригады">
        <div className="timeline__row timeline__row--header">
          <div className="timeline__track">
            <TimelineTicks scale={scale} />
          </div>
        </div>
        <div className="timeline__row">
          <TimelineTrack
            row={row}
            color={engineerColor(engineer.id, engineerIdsOf(state))}
            nowLeft={percent(scale, toMinutes(clock))}
            selectedRequestId={null}
            onSelect={selectRequest}
          />
        </div>
      </div>
      <div className="explanation__verdict">
        {stops.length > 0 ? (
          <p className="muted">
            {`Визитов: ${stops.length}${equipment} · пробег ${formatKm(summary.totalKm)} · в пути ${formatDuration(summary.travelMin)} · окончание работ ${summary.endOfWork}, конец смены ${engineer.shift_end}`}
          </p>
        ) : (
          <p className="muted">В этом плане у инженера нет визитов.</p>
        )}
        <WhyButton />
      </div>
      {stops.length > 0 && (
        <table className="table table--compact">
          <thead>
            <tr>
              <th>№</th>
              <th>Заявка</th>
              <th>Окно</th>
              <th>Приезд</th>
              <th>Начало</th>
              <th title="Сколько минут остаётся от начала работ до конца окна">Запас</th>
              <th title="Пробег от предыдущей точки">Путь</th>
            </tr>
          </thead>
          <tbody>
            {routeRows(summary).map((row) => {
              if (row.kind === 'lunch') {
                return (
                  <tr key="__lunch" className="route-card__lunch">
                    <td />
                    <td>Обед</td>
                    <td colSpan={5}>{formatWindow(row.lunch.start, row.lunch.end)}</td>
                  </tr>
                );
              }
              const { stop } = row;
              return (
                <tr key={stop.visit.request_id} className="route-card__row" onClick={() => selectRequest(stop.visit.request_id)}>
                  <td>{stop.order}</td>
                  <td>
                    <strong>{stop.visit.request_id}</strong>
                    {stop.visit.pinned && <span className="badge">Закреплена</span>}
                  </td>
                  <td>{stop.request ? requestWindowText(stop.request) : '—'}</td>
                  <td>{stop.visit.arrival}</td>
                  <td>{stop.visit.start}</td>
                  <td className={stop.slackMin !== null && stop.slackMin < 0 ? 'warn-text' : undefined}>{slackText(stop)}</td>
                  <td>{formatKm(stop.visit.leg_km)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {events.length > 0 && (
        <>
          <h4>События бригады</h4>
          <ul className="brigade-events" aria-label="События бригады">
            {events.map((item) => (
              <li key={item.id}>
                <span className="brigade-events__time">{item.event.time}</span>{' '}
                <span>
                  {describeEvent(item.event, engineers)}{' '}
                  <span className={`brigade-events__status brigade-events__status--${item.status}`}>{`· ${timelineStatusText(item)}`}</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
