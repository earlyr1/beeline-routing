-- depends: 0001.day-state
--
-- Ключи шагов шкалы в едином потоке событий (docs/superpowers/specs/2026-09-24-unified-events-design.md).
--
-- Раньше событие, стратегию которого диспетчер не выбирал и которое не было «ломающим» (правка заявки, отмена,
-- возврат), шло с «Оптимально по дню» под токеном без стратегии: 'tl_4'. Теперь у события без выбора считаются
-- три стратегии ('tl_4@optimal', '@stable', '@keep'), и окно выбора решает результат. Без перевода проход шкалы
-- не нашёл бы старых шагов: хвост дня после первого такого события считался бы заново, а давно применённое
-- событие могло бы остановить часы и потребовать выбора. Поэтому такое событие становится тем, чем и было, —
-- событием с выбранной «Оптимально по дню», а его шаги переезжают под новые ключи. Планы не меняются ни на байт.
--
-- Отката нет и не нужно (0002.unified-event-steps.rollback.sql пустой): прежняя сборка читает переведённый день
-- так же — у события со стратегией она сама ищет шаг под 'tl_4@optimal'.

-- Событие со старым шагом получает стратегию, с которой этот шаг посчитан.
UPDATE timeline_entries AS entry
SET variant = 'optimal'
WHERE entry.variant IS NULL
  AND EXISTS (
      SELECT 1
      FROM plans
      WHERE plans.dataset_id = entry.dataset_id
        AND (plans.token = entry.entry_id OR entry.entry_id = ANY (plans.prefix))
  );

-- Шаги со старыми токенами — в самом токене или в префиксе — записываются под новыми ключами. Копией, а не UPDATE
-- на месте: если шаг с новым ключом уже есть, остаётся он, как везде остаётся первый посчитанный шаг
-- (ON CONFLICT DO NOTHING в app/state/postgres.py), и уникальный ключ не мешает переводу.
INSERT INTO plans (dataset_id, prefix, token, applied, reason, session, created_at)
SELECT dataset_id,
       ARRAY(
           SELECT CASE WHEN strpos(item, '@') = 0 THEN item || '@optimal' ELSE item END
           FROM unnest(prefix) WITH ORDINALITY AS items (item, position)
           ORDER BY position
       ),
       CASE WHEN strpos(token, '@') = 0 THEN token || '@optimal' ELSE token END,
       applied,
       reason,
       session,
       created_at
FROM plans
WHERE token <> ''
  AND (strpos(token, '@') = 0 OR EXISTS (SELECT 1 FROM unnest(prefix) AS item WHERE strpos(item, '@') = 0))
ON CONFLICT DO NOTHING;

-- Старые ключи больше никто не ищет. Утренний план (пустой токен) остаётся как был.
DELETE FROM plans
WHERE token <> ''
  AND (strpos(token, '@') = 0 OR EXISTS (SELECT 1 FROM unnest(prefix) AS item WHERE strpos(item, '@') = 0));
