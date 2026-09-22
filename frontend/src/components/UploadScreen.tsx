import { useEffect, useRef, useState, type DragEvent } from 'react';
import type { GeocodePrecision } from '../api/types';
import { MATRIX_SOURCE_LABELS, PRECISION_LABELS, SOURCE_LABELS, STAGE_LABELS, plural } from '../lib/format';
import { MAX_WORKLOAD_LEVEL, MIN_WORKLOAD_LEVEL, WORKLOAD_LEVELS, travelBufferText, workloadLevel } from '../lib/workload';
import { useAppStore } from '../store/useAppStore';

const PRECISIONS: GeocodePrecision[] = ['house', 'street', 'locality', 'none'];

const LUNCH_HINT = '45 минут в середине смены у каждого инженера. С обедом план считается до 30 секунд, без обеда до 5.';

export function UploadScreen() {
  const status = useAppStore((s) => s.datasetStatus);
  const busy = useAppStore((s) => s.busy);
  const error = useAppStore((s) => s.error);
  const upload = useAppStore((s) => s.upload);
  const scenarios = useAppStore((s) => s.scenarios);
  const loadScenarios = useAppStore((s) => s.loadScenarios);
  const startScenario = useAppStore((s) => s.startScenario);
  const plan = useAppStore((s) => s.plan);
  const level = useAppStore((s) => s.workloadLevel);
  const setWorkloadLevel = useAppStore((s) => s.setWorkloadLevel);
  const lunchEnabled = useAppStore((s) => s.lunchEnabled);
  const setLunchEnabled = useAppStore((s) => s.setLunchEnabled);
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    void loadScenarios();
  }, [loadScenarios]);

  const start = (file: File | undefined) => {
    // Перетаскивание идёт мимо кнопок, поэтому файл во время предподсчёта или расчёта не открывается и здесь.
    if (!file || waiting) return;
    setFileName(file.name);
    void upload(file);
  };

  const openScenario = (region: string) => {
    // Файл диспетчер не выбирал: имя прежнего файла со страницы уходит.
    setFileName(null);
    void startScenario(region);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    start(event.dataTransfer.files[0]);
  };

  const processing = status?.status === 'processing';
  // Новый день не открывается, пока идёт предподсчёт или расчёт плана: второе нажатие бросило бы уже идущий поиск.
  const waiting = processing || busy;
  const report = status?.report ?? null;
  // Названия сгенерированных нами регионов из ответа сервера: подпись под рядом кнопок называет их сама.
  const generatedTitles = scenarios
    .filter((scenario) => scenario.generated)
    .map((scenario) => scenario.title)
    .join(', ');
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
          <button type="button" className="btn btn-primary" onClick={() => inputRef.current?.click()} disabled={waiting}>
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

        {scenarios.length > 0 && (
          <section className="scenarios" aria-labelledby="scenarios-title">
            {/* Главный путь по ТЗ — сырая выгрузка, поэтому регионы стоят под выбором файла, а не над ним. */}
            <h2 id="scenarios-title">Или открыть день региона</h2>
            <div className="scenarios__row">
              {scenarios.map((scenario) => (
                <button
                  key={scenario.region}
                  type="button"
                  className="btn scenario"
                  disabled={waiting}
                  onClick={() => openScenario(scenario.region)}
                >
                  {/* Регион без выгрузки Билайна подписан прямо на кнопке, а не в подсказке. */}
                  <span className="scenario__title">
                    {scenario.generated ? `${scenario.title} (сгенерирован нами)` : scenario.title}
                  </span>
                  <span className="scenario__meta">
                    {scenario.requests} {plural(scenario.requests, 'заявка', 'заявки', 'заявок')} ·{' '}
                    {scenario.engineers} {plural(scenario.engineers, 'бригада', 'бригады', 'бригад')}
                  </span>
                </button>
              ))}
            </div>
            <p className="muted">Те же выгрузки, уже разобранные: день открывается сразу, без выбора файла.</p>
            {generatedTitles && (
              // Про сгенерированный регион говорим сами: «те же выгрузки» к нему не относится.
              <p className="muted">
                {`${generatedTitles} — наш регион: выгрузки Билайна по нему нет, адреса и бригады сгенерированы нами.`}
              </p>
            )}
          </section>
        )}

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
            {/* Пометка держится и после нажатия кнопки: иначе сгенерированный день не отличить от настоящего. */}
            <h2>{report.generated ? `${report.region_title} — сгенерирован нами` : report.region_title}</h2>
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
                <dd>{SOURCE_LABELS[report.source]}</dd>
              </div>
            </dl>
            {report.generated && (
              <p className="note report__generated">
                {`Выгрузки Билайна по региону «${report.region_title}» нет: адреса, бригады и распределение «диспетчеров» сгенерированы нами (docs/assumptions.md).`}
              </p>
            )}
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
