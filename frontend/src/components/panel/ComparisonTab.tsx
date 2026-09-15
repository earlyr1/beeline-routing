import { kmPerEngineerRows, metricRows } from '../../lib/comparison';
import { formatKm } from '../../lib/format';
import { useAppStore } from '../../store/useAppStore';

const kmCell = (value: number | null) => (value === null ? '—' : formatKm(value));

export function ComparisonTab() {
  const state = useAppStore((s) => s.state);
  if (!state) return null;
  const rows = metricRows(state);
  const km = kmPerEngineerRows(state);

  return (
    <div className="comparison">
      <p className="muted">
        Базовый вариант по ТЗ (п. 2.3): заявки по порядку поступления первому подходящему инженеру, без оптимизации.
        Диспетчеры: фактическое распределение из контрольного файла, пересчитанное нашей моделью времени и пробега.
      </p>
      {state.events.length > 0 && (
        <p className="note">{`Колонка «Диспетчеры» — исходный день, события (${state.events.length}) в ней не учтены.`}</p>
      )}
      <table className="table">
        <thead>
          <tr>
            <th>Показатель</th>
            <th>Базовый (FCFS)</th>
            <th>Оптимизированный</th>
            <th>Диспетчеры</th>
            <th>Оптимизированный к базовому</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <td>{row.label}</td>
              <td>{row.baseline}</td>
              <td>
                <strong>{row.optimized}</strong>
              </td>
              <td>{row.dispatchers}</td>
              <td className={`delta delta--${row.verdict}`}>{row.delta}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h4>Пробег по инженерам</h4>
      <table className="table">
        <thead>
          <tr>
            <th>Инженер</th>
            <th>Базовый</th>
            <th>Оптимизированный</th>
            <th>Диспетчеры</th>
          </tr>
        </thead>
        <tbody>
          {km.map((row) => (
            <tr key={row.engineerId}>
              <td>{row.name}</td>
              <td>{kmCell(row.baseline)}</td>
              <td>{kmCell(row.optimized)}</td>
              <td>{kmCell(row.dispatchers)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
