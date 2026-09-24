import { Fragment, useEffect, useRef } from 'react';
import { clockAtDayEnd, dayOver, daySummary } from '../lib/daySummary';
import { plural } from '../lib/format';
import { AUTO_VARIANT_NOTE } from '../lib/variants';
import { useAppStore } from '../store/useAppStore';
import { Metric } from './MetricsStrip';

/** Что считает «с окном выбора»: из плана не видно, открывалось ли окно, видно только, кто выбрал вариант. */
const CHOSEN_TITLE = 'Вариант события выбрал диспетчер: в окне «Как исправить план» или сразу, как при отмене из «Коммуникаций»';

/**
 * Окно выпадает один раз на приход часов в конец дня: проигрыванием или ползунком. Закрытое окно не выпадает снова,
 * пока часы не уйдут назад и не вернутся. Поверх окна выбора варианта итоги не показываются: сначала выбор.
 */
function useDayEndArrival(open: () => void): void {
  const atEnd = useAppStore((s) => s.state !== null && clockAtDayEnd(s.state, s.clock));
  // Итоги считаются по плану на экране: он уже в конце дня, часы стоят, и ничего не пересчитывается.
  const settled = useAppStore(
    (s) =>
      s.state !== null &&
      dayOver(s.state, s.clock) &&
      !s.dragging &&
      !s.playing &&
      !s.committing &&
      !s.busy &&
      s.choice === null &&
      !s.choiceLoading,
  );
  // План, открытый уже в конце дня (после перезагрузки страницы), окно не выбрасывает: прихода не было,
  // итоги открывает кнопка у часов.
  const armed = useRef(false);
  useEffect(() => {
    if (!atEnd) {
      armed.current = true;
      return;
    }
    if (!settled || !armed.current) return;
    armed.current = false;
    open();
  }, [atEnd, settled, open]);
}

/**
 * Итоги дня: утренний план против итога, итог против базового FCFS и диспетчеров, события дня и звонки.
 * Считаются по плану на экране, ничего не спрашивая у сервера.
 */
function DaySummaryDialog() {
  const state = useAppStore((s) => s.state);
  const agreed = useAppStore((s) => s.agreed);
  const clock = useAppStore((s) => s.clock);
  const grid = useAppStore((s) => s.config?.window_grid) ?? [];
  const close = useAppStore((s) => s.closeDaySummary);
  const setTab = useAppStore((s) => s.setTab);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Итоги — верхний слой: Esc закрывает только их.
      event.preventDefault();
      close();
    };
    document.addEventListener('keydown', closeOnEscape, true);
    return () => document.removeEventListener('keydown', closeOnEscape, true);
  }, [close]);

  if (!state) return null;
  const summary = daySummary(state, agreed, clock, grid);
  const { events, calls } = summary;

  return (
    <div className="choice-overlay">
      <section className="choice choice--summary" role="dialog" aria-modal="true" aria-labelledby="day-summary-title">
        <header className="choice__head">
          <div>
            <p className="choice__eyebrow">{state.office.title}</p>
            <h2 id="day-summary-title">Итоги дня</h2>
          </div>
          <button type="button" className="btn btn-ghost btn-small" aria-label="Закрыть итоги" onClick={close}>
            ✕
          </button>
        </header>
        <p className="muted day-summary__line">{`Утро — план на начало дня, до событий. Итог — план на ${summary.end}.`}</p>
        <table className="table">
          <thead>
            <tr>
              <th>Показатель</th>
              <th>Утро</th>
              <th>Итог дня</th>
              <th>Разница</th>
            </tr>
          </thead>
          <tbody>
            {summary.rows.map((row) => (
              <tr key={row.label} className={row.nested ? 'day-summary__nested' : undefined}>
                <td>{row.label}</td>
                <td>{row.morning}</td>
                <td>
                  <strong>{row.day}</strong>
                </td>
                <td className={`delta delta--${row.verdict}`}>{row.delta}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="day-summary__line">
          {summary.versus.map((versus, index) => (
            <Fragment key={versus.against}>
              {index > 0 && ' · '}
              {`${index === 0 ? 'Итог дня ' : ''}${versus.against}`}
              {versus.note && <span className="muted">{` (${versus.note})`}</span>}
              {': '}
              {versus.deltas.map((delta, position) => (
                <Fragment key={delta.text}>
                  {position > 0 && ', '}
                  <span className={`delta--${delta.verdict}`}>{delta.text}</span>
                </Fragment>
              ))}
            </Fragment>
          ))}
        </p>

        <h4>События дня</h4>
        {events.total === 0 ? (
          <p className="muted day-summary__line">Событий за день не было: итог — утренний план.</p>
        ) : (
          <>
            <div className="day-summary__figures">
              {events.kinds.map((kind) => (
                <Metric key={kind.kind} label={kind.label} value={String(kind.count)} />
              ))}
            </div>
            <p className="muted day-summary__line">
              {`Всего применено: ${events.total} · `}
              <span title={CHOSEN_TITLE}>{`с окном выбора: ${events.chosen}`}</span>
              {` · без него: ${events.auto} — ${AUTO_VARIANT_NOTE}`}
            </p>
          </>
        )}

        <h4>Коммуникации</h4>
        <div className="day-summary__figures">
          <Metric
            label="Согласовано"
            value={String(calls.agreed)}
            title="Клиенты, с которыми договорились по телефону: назвали окно или сказали, что сегодня не приедем"
          />
          <Metric
            label="Ждут звонка"
            value={String(calls.waiting)}
            warn={calls.waiting > 0}
            title="Обещанное окно не выполняется, а клиенту ещё не позвонили: те же строки, что во вкладке «Коммуникации»"
          />
        </div>
        {calls.waiting > 0 && (
          <p className="note day-summary__line">
            {`Недоделка дня: ${calls.waiting} ${plural(calls.waiting, 'клиент ещё ждёт', 'клиента ещё ждут', 'клиентов ещё ждут')} звонка — вкладка «Коммуникации».`}
          </p>
        )}

        <div className="dialog__actions">
          <button
            type="button"
            className="btn"
            onClick={() => {
              setTab('comparison');
              close();
            }}
          >
            Открыть «Сравнение»
          </button>
          {/* Итоги прочитаны — Enter закрывает окно сразу после его появления. */}
          <button type="button" className="btn btn-primary" autoFocus onClick={close}>
            Закрыть
          </button>
        </div>
      </section>
    </div>
  );
}

/** Окно «Итоги дня»: выпадает само в конце дня, а после закрытия его открывает кнопка «Итоги дня» у часов. */
export function DaySummary() {
  const open = useAppStore((s) => s.daySummaryOpen);
  const openDaySummary = useAppStore((s) => s.openDaySummary);
  useDayEndArrival(openDaySummary);
  return open ? <DaySummaryDialog /> : null;
}
