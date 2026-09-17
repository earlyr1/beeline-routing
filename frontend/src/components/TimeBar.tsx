import { useEffect, useState } from 'react';
import { describeEvent, eventRequestId } from '../lib/events';
import { fromMinutes } from '../lib/format';
import { byId, displayedPlan } from '../lib/planView';
import { pinLeft, sliderRange, sliderValue, timelineItemTitle, timelinePins, timelineStatusText } from '../lib/timeBar';
import { dayScale, hourTicks } from '../lib/timeline';
import { VARIANT_TITLES } from '../lib/variants';
import { useAppStore } from '../store/useAppStore';

/** Пункты меню «Добавить событие»: открывают те же диалоги, что страница бригады, со временем на часах. */
const ADD_EVENT_ITEMS: { label: string; open(): void }[] = [
  { label: 'Срочная заявка', open: () => useAppStore.getState().openToolbarDialog('urgent') },
  // Инженера с часов не выбирали: диалоги сами предлагают самого загруженного после времени события.
  { label: 'Инженер заболел', open: () => useAppStore.getState().openEngineerDialog('unavailable', null) },
  { label: 'Поломка транспорта', open: () => useAppStore.getState().openEngineerDialog('transport', null) },
  { label: 'Задержка инженера', open: () => useAppStore.getState().startDelay(null) },
];

/**
 * Часы дня под верхней панелью: запуск и пауза, время, ползунок по шкале дня с отметками событий шкалы
 * и меню «Добавить событие». Пока диспетчер тянет ползунок, план не пересчитывается: только когда отпустит.
 */
export function TimeBar() {
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
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
  const [menuOpen, setMenuOpen] = useState(false);

  // Часы идут, только пока шкала на экране.
  useEffect(() => () => useAppStore.getState().stopPlayback(), []);

  // Список событий отметки и меню лежат выше окна выбора: открылось окно — они закрываются.
  useEffect(() => {
    if (!choosing) return;
    setOpenMinute(null);
    setMenuOpen(false);
  }, [choosing]);

  useEffect(() => {
    if (openMinute === null && !menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Esc закрывает только это меню: панель «Почему» под ним остаётся открытой.
      event.preventDefault();
      setOpenMinute(null);
      setMenuOpen(false);
    };
    // Перехват раньше обычных слушателей документа, чтобы они увидели, что Esc уже занят.
    document.addEventListener('keydown', closeOnEscape, true);
    return () => document.removeEventListener('keydown', closeOnEscape, true);
  }, [openMinute, menuOpen]);

  if (!state) return null;

  const range = sliderRange(dayScale(state, displayedPlan(state, showPrevious)));
  const pins = timelinePins(state.timeline ?? [], range);
  const openPin = pins.find((pin) => pin.minute === openMinute) ?? null;
  const engineers = byId(state.engineers);
  // События меняют текущий план: в плане до события и во время расчёта их не добавляют, как и с карты.
  const menuLocked = busy || showPrevious;
  // Удаление и смена варианта пересчитывают план: не во время расчёта, фиксации часов, проигрывания и открытого окна выбора.
  const deleteLocked = busy || committing || playing || choosing;
  const status = committing ? 'Пересчитываем план…' : state.timeline_ready === false ? 'Готовим события…' : null;

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
              title={pin.items.map((item) => timelineItemTitle(item, engineers)).join('\n')}
              aria-expanded={pin.minute === openMinute}
              onClick={() => {
                setMenuOpen(false);
                setOpenMinute((current) => (current === pin.minute ? null : pin.minute));
              }}
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
                      {describeEvent(item.event, engineers)}
                    </button>
                  ) : (
                    <span>{describeEvent(item.event, engineers)}</span>
                  )}
                  <span className={`time-bar__event-status time-bar__event-status--${item.status}`}>{timelineStatusText(item)}</span>
                  {item.choosable && (
                    <>
                      <span className="muted">{item.variant ? `Вариант: ${VARIANT_TITLES[item.variant]}` : 'Вариант не выбран'}</span>
                      <button
                        type="button"
                        className="btn btn-small"
                        disabled={deleteLocked || item.status === 'rejected'}
                        onClick={() => {
                          setOpenMinute(null);
                          void openChoice(item.id);
                        }}
                      >
                        Варианты…
                      </button>
                    </>
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
        <div className="time-bar__menu">
          <button
            type="button"
            className="btn btn-small btn-primary"
            aria-haspopup="menu"
            aria-expanded={menuOpen && !menuLocked}
            disabled={menuLocked}
            onClick={() => {
              setOpenMinute(null);
              setMenuOpen((open) => !open);
            }}
          >
            Добавить событие
          </button>
          {menuOpen && !menuLocked && (
            <div className="time-bar__menu-list" role="menu" aria-label="Добавить событие">
              {ADD_EVENT_ITEMS.map((item) => (
                <button
                  key={item.label}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    item.open();
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
