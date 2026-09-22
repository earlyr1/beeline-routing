-- Откат 0001: таблицы сносятся в обратном порядке, дети раньше родителя.
--
-- Операторов здесь ровно столько же, сколько в 0001.day-state.sql, и в зеркальном порядке: yoyo сшивает
-- шаги применения с шагами отката по позиции, и лишний CREATE без пары сдвинул бы все следующие.
-- Поэтому у CREATE INDEX есть свой DROP INDEX, хотя таблица унесла бы индекс и так.
DROP TABLE proposals;
DROP TABLE agreed_windows;
DROP TABLE plans;
DROP INDEX IF EXISTS timeline_entries_order_idx;
DROP TABLE timeline_entries;
DROP TABLE days;
