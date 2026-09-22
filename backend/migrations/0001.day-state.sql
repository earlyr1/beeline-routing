-- День диспетчера: входные данные, шкала событий, готовые планы, согласованные окна и предложения помощника.
--
-- Планы ХРАНЯТСЯ, а не выводятся заново: солвер ограничен по времени, и повтор того же расчёта даёт другие
-- маршруты. После перезапуска диспетчер должен увидеть тот же план, что минуту назад, поэтому единица
-- хранения — запись кэша шагов таймлайна (app/planning/timeline.py).

-- День: входные данные, отчёт предподсчёта, текущее время и счётчики номеров.
CREATE TABLE days (
    dataset_id     text PRIMARY KEY,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    -- processing | ready | failed: то же, что в DatasetStatus.
    status         text        NOT NULL DEFAULT 'processing',
    stage          text        NOT NULL DEFAULT 'parsing',
    error          text,
    report         jsonb,                                  -- UploadReport
    -- PreparedDay: регион, название, офис, заявки, бригады, контрольный план, сгенерирован ли регион.
    prepared       jsonb,
    cursor_min     integer     NOT NULL DEFAULT 0,         -- текущее время плана, минуты от полуночи
    revision       integer     NOT NULL DEFAULT 0,         -- Timeline.revision
    day_revision   integer     NOT NULL DEFAULT 0,         -- ревизия сразу после сборки дня
    last_number    integer     NOT NULL DEFAULT 0,         -- номера событий tl_<n> не повторяются
    last_version   integer     NOT NULL DEFAULT 0,         -- номера планов не повторяются
    urgent_number  integer     NOT NULL DEFAULT 0          -- URG-AI-<n> из чата с помощником
);

-- События шкалы. Порядок применения — по (time_min, seq), как TimelineEntry.order.
CREATE TABLE timeline_entries (
    dataset_id text    NOT NULL REFERENCES days (dataset_id) ON DELETE CASCADE,
    entry_id   text    NOT NULL,                    -- tl_7
    seq        integer NOT NULL,                    -- порядок добавления
    time_min   integer NOT NULL,                    -- event.time, минуты от полуночи
    event      jsonb   NOT NULL,                    -- Event как его прислали
    geo        jsonb   NOT NULL DEFAULT '{}',       -- ответы геокодера на новые адреса события
    checked    boolean NOT NULL DEFAULT false,      -- событие из подтверждённого предложения помощника
    variant    text,                                -- выбранная стратегия или NULL
    PRIMARY KEY (dataset_id, entry_id)
);

CREATE INDEX timeline_entries_order_idx ON timeline_entries (dataset_id, time_min, seq);

-- Кэш шагов: готовый план после применения события. Утренний план дня — prefix '{}', token ''.
CREATE TABLE plans (
    dataset_id text   NOT NULL REFERENCES days (dataset_id) ON DELETE CASCADE,
    prefix     text[] NOT NULL,   -- принятые события до шага, токенами
    token      text   NOT NULL,   -- 'tl_3@keep' | 'tl_4' | '' у утреннего плана
    applied    jsonb,             -- AppliedEvent; NULL у отклонённого шага и у утреннего плана
    reason     text,              -- событие отклонено: план остаётся прежним
    -- Сессия без матрицы дороги и без того, что лежит в days.prepared. NULL у отклонённого шага:
    -- его план — план предыдущего шага.
    session    jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, prefix, token)
);

-- Согласованные окна вкладки «Коммуникации»: что клиенту сказали по телефону.
CREATE TABLE agreed_windows (
    dataset_id     text NOT NULL REFERENCES days (dataset_id) ON DELETE CASCADE,
    request_id     text NOT NULL,
    client_window  jsonb,         -- {start,end,asap} или NULL — «сегодня не приедем»
    request_window jsonb,         -- окно самой заявки в момент разговора
    version        integer,       -- номер плана, на котором договорились
    agreed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, request_id)
);

-- Предложения помощника: их статусы переживают перезапуск вместе с днём.
CREATE TABLE proposals (
    dataset_id  text    NOT NULL REFERENCES days (dataset_id) ON DELETE CASCADE,
    proposal_id text    NOT NULL,      -- pr_3
    seq         integer NOT NULL,      -- порядок показа
    status      text    NOT NULL,      -- pending | approved | rejected | failed
    payload     jsonb   NOT NULL,      -- Proposal целиком
    PRIMARY KEY (dataset_id, proposal_id)
);
