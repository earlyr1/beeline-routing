import { useEffect, useState } from 'react';
import { describeEvent, eventRequestId } from '../lib/events';
import { fromMinutes } from '../lib/format';
import { byId } from '../lib/planView';
import { pinLeft, sliderRange, sliderValue, timelineItemTitle, timelinePins, timelineStatusText } from '../lib/timeBar';
import { dayScale, hourTicks } from '../lib/timeline';
import { timelineVariantText } from '../lib/variants';
import { useAppStore } from '../store/useAppStore';

/**
 * Часы дня под верхней панелью: запуск и пауза, время, ползунок по шкале дня с отметками событий шкалы.
 * Пока диспетчер тянет ползунок, план не пересчитывается: только когда отпустит.
 */
export function TimeBar() {
  const state = useAppStore((s) => s.state);
  const clock = useAppStore((s) => s.clock);
  const playing = useAppStore((s) => s.playing);
  const committing = useAppStore((s) => s.committing);
  const busy = useAppStore((s) => s.busy);
  const setClock = useAppStore((s) => s.setClock);
  const commitClock = useAppStore((s) => s.commitClock);
  const play = useAppStore((s) => s.play);
  const pause = useAppStore((s) => s.pause);
  const startDrag = useAppStore((s) => s.startDrag);
  const endDrag = useAppStore((s) => s.endDrag);
  const deleteTimelineEvent = useAppStore((s) => s.deleteTimelineEvent);
  const openChoice = useAppStore((s) => s.openChoice);
  const selectRequest = useAppStore((s) => s.selectRequest);
  // Пока открыто окно выбора варианта, часы стоят на событии: ни запуска, ни ползунка.
  const choosing = useAppStore((s) => s.choice !== null || s.choiceLoading);
  /** Минута отметки, чьи события открыты; null — список закрыт. */
  const [openMinute, setOpenMinute] = useState<number | null>(null);

  // Часы идут, только пока шкала на экране.
  useEffect(() => () => useAppStore.getState().stopPlayback(), []);

  // Список событий отметки лежит выше окна выбора: открылось окно — он закрывается.
  useEffect(() => {
    if (!choosing) return;
    setOpenMinute(null);
  }, [choosing]);

  useEffect(() => {
    if (openMinute === null) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Esc закрывает только этот список: панель «Почему» под ним остаётся открытой.
      event.preventDefault();
      setOpenMinute(null);
    };
    // Перехват раньше обычных слушателей документа, чтобы они увидели, что Esc уже занят.
    document.addEventListener('keydown', closeOnEscape, true);
    return () => document.removeEventListener('keydown', closeOnEscape, true);
  }, [openMinute]);

  if (!state) return null;

  const range = sliderRange(dayScale(state));
  const pins = timelinePins(state.timeline ?? [], range);
  const openPin = pins.find((pin) => pin.minute === openMinute) ?? null;
  const engineers = byId(state.engineers);
  const requests = byId(state.requests);
  // Удаление и смена варианта пересчитывают план: не во время расчёта, фиксации часов, проигрывания и открытого окна выбора.
  const deleteLocked = busy || committing || playing || choosing;
  // Событие без выбранной стратегии сервер считает сразу тремя, а нужно ли окно выбора, видно только из ответа:
  // пока он не пришёл, о расчёте говорит строка у часов, а не окно выбора.
  const status = committing || busy ? 'Пересчитываем план…' : state.timeline_ready === false ? 'Готовим события…' : null;

  // pointerup, pointercancel и lostpointercapture приходят вместе: план пересчитывается один раз.
  const release = () => {
    if (useAppStore.getState().dragging) endDrag();
  };

  const remove = async (entryId: string) => {
    if (await deleteTimelineEvent(entryId)) setOpenMinute(null);
  };

  return (
    <section className="time-bar" aria-label="Время дня">
      <div className="time-bar__controls">
        <button
          type="button"
          className={`btn time-bar__play${playing ? ' time-bar__play--playing' : ''}`}
          aria-label={playing ? 'Пауза' : 'Запустить'}
          title={playing ? 'Остановить часы' : 'Запустить часы: час дня за 6 секунд'}
          disabled={choosing}
          onClick={() => (playing ? pause() : play())}
        >
          <span aria-hidden="true">{playing ? '❚❚' : '▶'}</span>
        </button>
        <span className="muted time-bar__tempo">1 ч = 6 с</span>
        <strong className="time-bar__clock" role="timer" aria-label="Время на часах">
          {clock}
        </strong>
      </div>
      <div className="time-bar__scale">
        <div className="time-bar__pins">
          {pins.map((pin) => (
            <button
              key={pin.minute}
              type="button"
              className={`time-bar__pin time-bar__pin--${pin.status}${pin.minute === openMinute ? ' time-bar__pin--open' : ''}`}
              style={{ left: `${pin.left}%` }}
              title={pin.items.map((item) => timelineItemTitle(item, engineers, requests)).join('\n')}
              aria-expanded={pin.minute === openMinute}
              onClick={() => setOpenMinute((current) => (current === pin.minute ? null : pin.minute))}
            >
              {pin.label}
              {pin.items.length > 1 && <span className="time-bar__pin-count">{pin.items.length}</span>}
            </button>
          ))}
        </div>
        <input
          type="range"
          className="time-bar__range"
          aria-label="Текущее время"
          min={range.min}
          max={range.max}
          step={1}
          value={sliderValue(clock, range)}
          disabled={choosing}
          onPointerDown={(event) => {
            // Указатель, отпущенный за пределами ползунка, иначе не вернул бы pointerup, и перетаскивание не закончилось бы.
            try {
              event.currentTarget.setPointerCapture?.(event.pointerId);
            } catch {
              // Браузер не дал захватить указатель: перетаскивание закончится на blur или keyup.
            }
            startDrag();
          }}
          onChange={(event) => setClock(fromMinutes(Number(event.target.value)))}
          onPointerUp={release}
          onPointerCancel={release}
          onLostPointerCapture={release}
          onKeyUp={() => void commitClock()}
          onBlur={() => void commitClock()}
        />
        <div className="time-bar__ticks" aria-hidden="true">
          {hourTicks({ from: range.min, to: range.max }).map((tick) => (
            <span key={tick} className="time-bar__tick" style={{ left: `${pinLeft(range, tick)}%` }}>
              {tick / 60}
            </span>
          ))}
        </div>
        {openPin && (
          <div
            className="time-bar__popover"
            role="group"
            aria-label={`События ${openPin.time}`}
            style={{ left: `${Math.min(85, Math.max(15, openPin.left))}%` }}
          >
            <div className="time-bar__popover-head">
              <strong>{`События ${openPin.time}`}</strong>
              <button type="button" className="btn btn-ghost btn-small" aria-label="Закрыть события" onClick={() => setOpenMinute(null)}>
                ✕
              </button>
            </div>
            <ul className="time-bar__events">
              {openPin.items.map((item) => {
                const requestId = eventRequestId(item.event);
                // Заявку события можно открыть, когда она уже есть в плане: срочная заявка впереди ещё не добавлена.
                const openable = requestId !== null && state.requests.some((request) => request.id === requestId);
                // Варианты есть у любого события, которое сервер не отклонил: окно выбора решает не тип события,
                // а результат, и стратегию, выбранную без окна, диспетчер может сменить. Кроме событий за тем, которое
                // ждёт выбора: до них сервер не дойдёт, пока диспетчер не выберет (choosable).
                const withVariants = item.choosable;
                // Без стратегии событие либо ждёт выбора, либо ещё не посчитано: «не выбран» — только про первое.
                const variantText =
                  timelineVariantText(item, state.engineers) ?? (item.status === 'awaiting' ? 'Вариант не выбран' : null);
                return (
                <li key={item.id} className="time-bar__event">
                  {openable ? (
                    <button
                      type="button"
                      className="link-button time-bar__event-link"
                      title="Открыть заявку"
                      onClick={() => {
                        setOpenMinute(null);
                        selectRequest(requestId);
                      }}
                    >
                      {describeEvent(item.event, engineers, requests)}
                    </button>
                  ) : (
                    <span>{describeEvent(item.event, engineers, requests)}</span>
                  )}
                  <span className={`time-bar__event-status time-bar__event-status--${item.status}`}>{timelineStatusText(item)}</span>
                  {/* У отклонённого события видна только стратегия, которую выбрал диспетчер: варианты он уже не сменит. */}
                  {variantText && <span className="muted">{variantText}</span>}
                  {withVariants && (
                    <button
                      type="button"
                      className="btn btn-small"
                      disabled={deleteLocked}
                      onClick={() => {
                        setOpenMinute(null);
                        void openChoice(item.id);
                      }}
                    >
                      Варианты…
                    </button>
                  )}
                  <button type="button" className="btn btn-small" disabled={deleteLocked} onClick={() => void remove(item.id)}>
                    Удалить событие
                  </button>
                </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
      <div className="time-bar__actions">
        {status && (
          <span className="muted time-bar__status" aria-live="polite">
            {status}
          </span>
        )}
      </div>
    </section>
  );
}
