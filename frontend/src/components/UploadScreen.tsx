import { useRef, useState, type DragEvent } from 'react';
import type { GeocodePrecision } from '../api/types';
import { MATRIX_SOURCE_LABELS, PRECISION_LABELS, STAGE_LABELS } from '../lib/format';
import { MAX_WORKLOAD_LEVEL, MIN_WORKLOAD_LEVEL, WORKLOAD_LEVELS, travelBufferText, workloadLevel } from '../lib/workload';
import { useAppStore } from '../store/useAppStore';

const PRECISIONS: GeocodePrecision[] = ['house', 'street', 'locality', 'none'];

const LUNCH_HINT = '45 минут в середине смены у каждого инженера. С обедом план считается до 15 секунд, без обеда до 5.';

export function UploadScreen() {
  const status = useAppStore((s) => s.datasetStatus);
  const busy = useAppStore((s) => s.busy);
  const error = useAppStore((s) => s.error);
  const upload = useAppStore((s) => s.upload);
  const plan = useAppStore((s) => s.plan);
  const level = useAppStore((s) => s.workloadLevel);
  const setWorkloadLevel = useAppStore((s) => s.setWorkloadLevel);
  const lunchEnabled = useAppStore((s) => s.lunchEnabled);
  const setLunchEnabled = useAppStore((s) => s.setLunchEnabled);
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const start = (file: File | undefined) => {
    if (!file) return;
    setFileName(file.name);
    void upload(file);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    start(event.dataTransfer.files[0]);
  };

  const processing = status?.status === 'processing';
  const report = status?.report ?? null;
  const progress = status && status.progress.total > 0 ? Math.round((status.progress.done / status.progress.total) * 100) : 0;
  // Пока файл обрабатывается, нагрузку и обед можно менять: сервер получит их только вместе с «Спланировать».
  const planning = busy && status?.status === 'ready';
  const workload = workloadLevel(level);

  return (
    <main className="upload-screen">
      <section className="upload-card">
        <h1>Планирование маршрутов выездных инженеров</h1>
        <p className="muted">
          Загрузите выгрузку заявок Билайна (CSV) или готовый набор данных (JSON). Сервис найдёт адреса на карте,
          посчитает расстояния и построит план на день.
        </p>

        <div
          className={`dropzone${dragging ? ' dropzone--active' : ''}`}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <p>Перетащите файл сюда или</p>
          <button type="button" className="btn btn-primary" onClick={() => inputRef.current?.click()} disabled={processing}>
            Загрузить CSV или JSON
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.json"
            hidden
            data-testid="file-input"
            onChange={(event) => start(event.target.files?.[0])}
          />
          {fileName && <p className="muted">Файл: {fileName}</p>}
        </div>

        {status && (
          <div className="upload-status" aria-live="polite">
            <div className="upload-status__row">
              <span>{STAGE_LABELS[status.stage]}</span>
              {status.progress.total > 0 && (
                <span>
                  {status.progress.done} из {status.progress.total}
                </span>
              )}
            </div>
            {processing && (
              <div className="progress">
                <div className="progress__bar" style={{ width: `${progress}%` }} />
              </div>
            )}
          </div>
        )}

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}

        {report && (
          <div className="report">
            <h2>{report.region_title}</h2>
            <dl className="report__grid">
              <div>
                <dt>Заявок</dt>
                <dd>{report.requests}</dd>
              </div>
              <div>
                <dt>Инженеров</dt>
                <dd>{report.engineers}</dd>
              </div>
              <div>
                <dt>Расстояния</dt>
                <dd>{MATRIX_SOURCE_LABELS[report.matrix_source]}</dd>
              </div>
              <div>
                <dt>Источник</dt>
                <dd>{report.source === 'bundle' ? 'Готовый набор JSON' : 'Выгрузка Билайна CSV'}</dd>
              </div>
            </dl>
            <p className="report__geo">
              Адреса на карте: {PRECISIONS.map((key) => `${PRECISION_LABELS[key]} ${report.geocoding[key] ?? 0}`).join(' · ')}
            </p>
            {report.not_found.length > 0 && (
              <details>
                <summary>Не найдены на карте: {report.not_found.length}</summary>
                <ul>
                  {report.not_found.map((item) => (
                    <li key={item.request_id}>
                      {item.request_id}: {item.address}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {report.skipped_rows.length > 0 && (
              <details>
                <summary>Пропущено строк: {report.skipped_rows.length}</summary>
                <ul>
                  {report.skipped_rows.map((row) => (
                    <li key={row}>{row}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}

        <section className="workload" aria-labelledby="workload-title">
          <h2 id="workload-title">Нагрузка инженеров</h2>
          <div className="workload__scale">
            <span className="workload__end" aria-hidden="true">
              {WORKLOAD_LEVELS[MIN_WORKLOAD_LEVEL].emoji}
            </span>
            <input
              type="range"
              className="workload__range"
              min={MIN_WORKLOAD_LEVEL}
              max={MAX_WORKLOAD_LEVEL}
              step={1}
              value={workload.level}
              aria-label="Нагрузка инженеров"
              aria-valuetext={workload.title}
              disabled={planning}
              onChange={(event) => setWorkloadLevel(Number(event.target.value))}
            />
            <span className="workload__end" aria-hidden="true">
              {WORKLOAD_LEVELS[MAX_WORKLOAD_LEVEL].emoji}
            </span>
          </div>
          <p className="workload__summary" aria-live="polite">
            {`${workload.emoji} ${workload.title}: ${workload.hint}`}
          </p>
          <p className="muted workload__buffer">{travelBufferText(workload.level)}</p>
          <label className="workload__lunch">
            <input
              type="checkbox"
              checked={lunchEnabled}
              disabled={planning}
              onChange={(event) => setLunchEnabled(event.target.checked)}
            />
            <span>Обед по плану</span>
          </label>
          <p className="muted workload__lunch-hint">{LUNCH_HINT}</p>
        </section>

        {status?.status === 'ready' && (
          <button type="button" className="btn btn-primary btn-large" onClick={() => void plan()} disabled={busy}>
            {busy ? 'Считаем план…' : 'Спланировать'}
          </button>
        )}
      </section>
    </main>
  );
}
