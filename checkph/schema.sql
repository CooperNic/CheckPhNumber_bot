-- Схема базы реестра нумерации. Три слоя:
--   1. реестр Минцифры: версионируемые диапазоны (SCD Type 2), операторы, выгрузки;
--   2. номер как сущность (строка появляется лениво, по факту данных);
--   3. обогащение: append-only наблюдения из внешних источников.
-- Скрипт идемпотентен.

-- ---------------------------------------------------------------------------
-- Слой 1. Реестр
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS import_batch (
    id           INTEGER PRIMARY KEY,
    source_date  TEXT    NOT NULL UNIQUE,                 -- дата выгрузки, YYYY-MM-DD
    imported_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    files_sha256 TEXT    NOT NULL,
    range_count  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS operator (
    id   INTEGER PRIMARY KEY,
    inn  TEXT UNIQUE CHECK (inn IS NULL OR length(inn) IN (10, 12)),
    name TEXT NOT NULL                                    -- актуальное (самое частое) написание
);

-- Оператор без ИНН идентифицируется по имени.
CREATE UNIQUE INDEX IF NOT EXISTS ix_operator_name_no_inn
    ON operator (name) WHERE inn IS NULL;

CREATE TABLE IF NOT EXISTS operator_name_variant (
    operator_id      INTEGER NOT NULL REFERENCES operator(id),
    name             TEXT    NOT NULL,
    first_seen_batch INTEGER NOT NULL REFERENCES import_batch(id),
    last_seen_batch  INTEGER NOT NULL REFERENCES import_batch(id),
    PRIMARY KEY (operator_id, name)
);

CREATE TABLE IF NOT EXISTS number_range (
    id               INTEGER PRIMARY KEY,
    code             INTEGER NOT NULL CHECK (code BETWEEN 100 AND 999),
    from_number      INTEGER NOT NULL CHECK (from_number BETWEEN 0 AND 9999999),
    to_number        INTEGER NOT NULL CHECK (to_number BETWEEN from_number AND 9999999),
    operator_id      INTEGER NOT NULL REFERENCES operator(id),
    region_raw       TEXT    NOT NULL,
    gar_raw          TEXT    NOT NULL,
    valid_from_batch INTEGER NOT NULL REFERENCES import_batch(id),
    valid_to_batch   INTEGER          REFERENCES import_batch(id)   -- NULL = действует
);

-- Текущее состояние: внутри снимка диапазоны одного кода не пересекаются,
-- поэтому (code, from_number) уникален среди открытых версий.
CREATE UNIQUE INDEX IF NOT EXISTS ix_range_current
    ON number_range (code, from_number) WHERE valid_to_batch IS NULL;

-- История: все версии, накрывавшие номер.
CREATE INDEX IF NOT EXISTS ix_range_all
    ON number_range (code, from_number, to_number);

-- Закрытие версий при импорте.
CREATE INDEX IF NOT EXISTS ix_range_valid_to
    ON number_range (valid_to_batch);

-- ---------------------------------------------------------------------------
-- Слой 2. Номер
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS phone_number (
    number     INTEGER PRIMARY KEY                        -- 7XXXXXXXXXX
               CHECK (number BETWEEN 71000000000 AND 79999999999),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Слой 3. Обогащение
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS data_source (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('bank', 'spam_db', 'user_report', 'other')),
    url  TEXT
);

CREATE TABLE IF NOT EXISTS number_owner_observation (
    id          INTEGER PRIMARY KEY,
    number      INTEGER NOT NULL REFERENCES phone_number(number),
    source_id   INTEGER NOT NULL REFERENCES data_source(id),
    owner_name  TEXT,
    owner_kind  TEXT CHECK (owner_kind IN ('person', 'company', 'unknown')),
    observed_at TEXT NOT NULL,
    raw_payload TEXT                                      -- JSON ответа источника как есть
);

CREATE INDEX IF NOT EXISTS ix_owner_obs_number
    ON number_owner_observation (number, observed_at DESC);

CREATE TABLE IF NOT EXISTS number_spam_observation (
    id          INTEGER PRIMARY KEY,
    number      INTEGER NOT NULL REFERENCES phone_number(number),
    source_id   INTEGER NOT NULL REFERENCES data_source(id),
    is_spam     INTEGER NOT NULL CHECK (is_spam IN (0, 1)),
    category    TEXT,
    score       REAL,
    observed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_spam_obs_number
    ON number_spam_observation (number, observed_at DESC);

CREATE TABLE IF NOT EXISTS lookup_event (
    id           INTEGER PRIMARY KEY,
    number       INTEGER,                                 -- NULL, если ввод не распознан
    raw_input    TEXT    NOT NULL,
    tg_user_id   INTEGER,
    requested_at TEXT    NOT NULL DEFAULT (datetime('now')),
    found        INTEGER NOT NULL CHECK (found IN (0, 1)),
    duration_ms  REAL
);

CREATE INDEX IF NOT EXISTS ix_lookup_event_number
    ON lookup_event (number);

-- Курсор синхронизации с порталом: что уже видели в индексе downloads/.
CREATE TABLE IF NOT EXISTS sync_state (
    filename           TEXT PRIMARY KEY,                  -- ABC-3xx.csv и т.п.
    remote_mtime       TEXT NOT NULL,                     -- дата из индекса nginx, как есть
    size_bytes         INTEGER,                           -- точный Content-Length при последней проверке
    content_sha256     TEXT,                              -- sha256 одного файла при последней проверке
    content_checked_at TEXT NOT NULL                      -- когда последний раз качали/сверяли содержимое
);

-- ---------------------------------------------------------------------------
-- Представления
-- ---------------------------------------------------------------------------

-- Последнее наблюдение владельца по каждому номеру.
CREATE VIEW IF NOT EXISTS v_number_owner_current AS
SELECT o.number, o.owner_name, o.owner_kind, o.observed_at, s.name AS source_name
FROM number_owner_observation AS o
JOIN data_source AS s ON s.id = o.source_id
WHERE o.id = (
    SELECT id FROM number_owner_observation
    WHERE number = o.number
    ORDER BY observed_at DESC, id DESC
    LIMIT 1
);

-- Спам-статус за последние 180 дней: отмечен хотя бы одним источником.
CREATE VIEW IF NOT EXISTS v_number_spam_current AS
SELECT number,
       max(is_spam)     AS is_spam,
       count(*)         AS observations,
       max(observed_at) AS last_observed_at
FROM number_spam_observation
WHERE observed_at >= datetime('now', '-180 days')
GROUP BY number;
