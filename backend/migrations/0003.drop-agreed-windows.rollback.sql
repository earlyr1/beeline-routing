-- Откат 0003: таблица отметок возвращается пустой, как в 0001.day-state.sql. Отметки, снесённые миграцией, не
-- возвращаются, а события «Коммуникация» остаются на шкале: прежняя сборка их не знает, и день с ними она не
-- поднимет. Откатывать 0003 имеет смысл только на базе, где таких событий ещё нет.
CREATE TABLE agreed_windows (
    dataset_id     text NOT NULL REFERENCES days (dataset_id) ON DELETE CASCADE,
    request_id     text NOT NULL,
    client_window  jsonb,         -- {start,end,asap} или NULL — «сегодня не приедем»
    request_window jsonb,         -- окно самой заявки в момент разговора
    version        integer,       -- номер плана, на котором договорились
    agreed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, request_id)
);
