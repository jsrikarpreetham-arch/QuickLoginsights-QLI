-- ============================================
-- EXTENSIONS
-- ============================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";


-- ============================================
-- ENUM TYPES (idempotent via DO block)
-- ============================================
DO $$ BEGIN
    CREATE TYPE log_level_enum AS ENUM ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE source_type_enum AS ENUM ('application', 'server', 'api', 'ai_model');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE severity_enum AS ENUM ('low', 'medium', 'high', 'critical');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE incident_status_enum AS ENUM ('open', 'investigating', 'resolved', 'suppressed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE detection_method_enum AS ENUM ('rule', 'anomaly', 'llm', 'combined');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;


-- ============================================
-- FUNCTION: AUTO UPDATE updated_at
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ============================================
-- TABLE: log_sources
-- ============================================
CREATE TABLE IF NOT EXISTS log_sources (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL UNIQUE,
    type        source_type_enum NOT NULL,
    config      JSONB DEFAULT '{}',
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_log_sources_updated ON log_sources;
CREATE TRIGGER trg_log_sources_updated
BEFORE UPDATE ON log_sources
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();


-- ============================================
-- TABLE: raw_logs (PARTITIONED)
-- ============================================
CREATE TABLE IF NOT EXISTS raw_logs (
    id            UUID DEFAULT uuid_generate_v4(),
    source_id     UUID NOT NULL REFERENCES log_sources(id),
    raw_payload   JSONB NOT NULL,
    checksum      TEXT NOT NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (id, received_at)
) PARTITION BY RANGE (received_at);


-- ============================================
-- RAW LOG PARTITIONS
-- ============================================
CREATE TABLE IF NOT EXISTS raw_logs_2026_05 PARTITION OF raw_logs
FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE IF NOT EXISTS raw_logs_2026_06 PARTITION OF raw_logs
FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE IF NOT EXISTS raw_logs_2026_07 PARTITION OF raw_logs
FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');


-- ============================================
-- RAW LOG INDEXES
-- ============================================
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_logs_source_checksum
ON raw_logs (source_id, checksum, received_at);

CREATE INDEX IF NOT EXISTS idx_raw_logs_received
ON raw_logs (received_at DESC);

CREATE INDEX IF NOT EXISTS idx_raw_logs_payload_gin
ON raw_logs USING GIN (raw_payload);


-- ============================================
-- TABLE: parsed_logs (PARTITIONED)
-- ============================================
CREATE TABLE IF NOT EXISTS parsed_logs (
    id                   UUID DEFAULT uuid_generate_v4(),

    source_id            UUID NOT NULL REFERENCES log_sources(id),

    raw_log_id           UUID,
    raw_log_received_at  TIMESTAMPTZ,

    log_level            log_level_enum NOT NULL,

    service_name         TEXT,
    host                 TEXT,

    message              TEXT NOT NULL,

    metadata             JSONB DEFAULT '{}',

    trace_id             TEXT,
    span_id              TEXT,

    duration_ms          DOUBLE PRECISION,
    status_code          INT,
    token_count          INT,

    timestamp            TIMESTAMPTZ NOT NULL,
    ingested_at          TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);


-- ============================================
-- PARSED LOG PARTITIONS
-- ============================================
CREATE TABLE IF NOT EXISTS parsed_logs_2026_05 PARTITION OF parsed_logs
FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE IF NOT EXISTS parsed_logs_2026_06 PARTITION OF parsed_logs
FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE IF NOT EXISTS parsed_logs_2026_07 PARTITION OF parsed_logs
FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');


-- ============================================
-- PARSED LOG INDEXES
-- ============================================
CREATE INDEX IF NOT EXISTS idx_parsed_logs_timestamp
ON parsed_logs (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_parsed_logs_level
ON parsed_logs (log_level);

CREATE INDEX IF NOT EXISTS idx_parsed_logs_service
ON parsed_logs (service_name);

CREATE INDEX IF NOT EXISTS idx_parsed_logs_source
ON parsed_logs (source_id);

CREATE INDEX IF NOT EXISTS idx_parsed_logs_trace
ON parsed_logs (trace_id);

CREATE INDEX IF NOT EXISTS idx_parsed_logs_msg_trgm
ON parsed_logs USING GIN (message gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_parsed_logs_metadata_gin
ON parsed_logs USING GIN (metadata);


-- ============================================
-- TABLE: detection_rules
-- ============================================
CREATE TABLE IF NOT EXISTS detection_rules (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    name         TEXT NOT NULL UNIQUE,
    description  TEXT,

    rule_type    TEXT NOT NULL,

    condition    JSONB NOT NULL,

    severity     severity_enum NOT NULL,

    enabled      BOOLEAN DEFAULT TRUE,

    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_detection_rules_updated ON detection_rules;
CREATE TRIGGER trg_detection_rules_updated
BEFORE UPDATE ON detection_rules
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();


-- ============================================
-- DETECTION RULE INDEXES
-- ============================================
CREATE INDEX IF NOT EXISTS idx_detection_rules_condition_gin
ON detection_rules USING GIN (condition);


-- ============================================
-- SEED RULES (idempotent via ON CONFLICT DO NOTHING)
-- ============================================
INSERT INTO detection_rules
(name, description, rule_type, condition, severity)
VALUES
(
    'high_error_rate',
    'Triggers when error rate exceeds 5% in a 5 minute window',
    'rate',
    '{"metric":"error_rate","threshold":0.05,"window_minutes":5}',
    'high'
),
(
    'critical_log_detected',
    'Triggers immediately on any CRITICAL log',
    'threshold',
    '{"log_level":"CRITICAL","count":1}',
    'critical'
),
(
    'high_latency_api',
    'API response time exceeds 2000ms',
    'threshold',
    '{"metric":"duration_ms","threshold":2000,"source_type":"api"}',
    'medium'
),
(
    'ai_model_token_spike',
    'AI model token usage spike (3x baseline)',
    'rate',
    '{"metric":"token_count","multiplier":3.0,"window_minutes":10}',
    'medium'
)
ON CONFLICT (name) DO NOTHING;


-- ============================================
-- TABLE: anomaly_baselines
-- ============================================
CREATE TABLE IF NOT EXISTS anomaly_baselines (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    source_id        UUID NOT NULL REFERENCES log_sources(id),

    metric_name      TEXT NOT NULL,

    baseline_value   DOUBLE PRECISION,
    std_dev          DOUBLE PRECISION,

    sample_count     INT,

    model_params     JSONB DEFAULT '{}',

    updated_at       TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (source_id, metric_name)
);

DROP TRIGGER IF EXISTS trg_anomaly_baselines_updated ON anomaly_baselines;
CREATE TRIGGER trg_anomaly_baselines_updated
BEFORE UPDATE ON anomaly_baselines
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();


-- ============================================
-- TABLE: incidents
-- ============================================
CREATE TABLE IF NOT EXISTS incidents (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    title                 TEXT NOT NULL,
    description           TEXT,

    severity              severity_enum NOT NULL,

    status                incident_status_enum DEFAULT 'open',

    detection_method      detection_method_enum NOT NULL,

    rule_id               UUID REFERENCES detection_rules(id),

    source_id             UUID REFERENCES log_sources(id),

    first_seen_at         TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at          TIMESTAMPTZ DEFAULT NOW(),

    resolved_at           TIMESTAMPTZ,

    occurrence_count      INT DEFAULT 1,

    root_cause_analysis   TEXT,

    metadata              JSONB DEFAULT '{}',

    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_incidents_updated ON incidents;
CREATE TRIGGER trg_incidents_updated
BEFORE UPDATE ON incidents
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();


-- ============================================
-- INCIDENT INDEXES
-- ============================================
CREATE INDEX IF NOT EXISTS idx_incidents_status
ON incidents (status);

CREATE INDEX IF NOT EXISTS idx_incidents_severity
ON incidents (severity);

CREATE INDEX IF NOT EXISTS idx_incidents_created
ON incidents (created_at DESC);


-- ============================================
-- TABLE: incident_logs
-- ============================================
CREATE TABLE IF NOT EXISTS incident_logs (
    incident_id      UUID NOT NULL,
    log_id           UUID NOT NULL,
    log_timestamp    TIMESTAMPTZ NOT NULL,

    PRIMARY KEY (incident_id, log_id, log_timestamp),

    CONSTRAINT fk_incident
    FOREIGN KEY (incident_id)
    REFERENCES incidents(id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_incident_logs_incident_id
ON incident_logs (incident_id);

CREATE INDEX IF NOT EXISTS idx_incident_logs_log_id
ON incident_logs (log_id);


-- ============================================
-- TABLE: llm_analyses
-- ============================================
CREATE TABLE IF NOT EXISTS llm_analyses (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    incident_id   UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,

    prompt        TEXT NOT NULL,
    response      TEXT NOT NULL,

    model_used    TEXT NOT NULL,

    tokens_used   INT,

    created_at    TIMESTAMPTZ DEFAULT NOW()
);
--- =============================================
--  TABLE: alerts
--  =============================================
CREATE TYPE alert_severity_enum AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL');

CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    severity alert_severity_enum NOT NULL DEFAULT 'MEDIUM',
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at TIMESTAMPTZ
);

CREATE INDEX ix_alerts_incident_created ON alerts (incident_id, created_at);
CREATE INDEX ix_alerts_severity ON alerts (severity);
CREATE INDEX ix_alerts_acknowledged ON alerts (acknowledged);
