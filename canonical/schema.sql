-- Efficiens canonical SQL layer
-- Baseline schema for authoritative structured state.
-- Apply to a new SQLite database; do not use this file to overwrite history.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS provenance (
    provenance_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_uri TEXT,
    source_ref TEXT,
    captured_at TEXT NOT NULL,
    retrieved_at TEXT,
    content_hash TEXT,
    freshness_at TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    scope TEXT,
    provenance_id TEXT REFERENCES provenance(provenance_id),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    superseded_by TEXT REFERENCES entities(entity_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    task_type TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    owner_entity_id TEXT REFERENCES entities(entity_id),
    scope TEXT,
    provenance_id TEXT REFERENCES provenance(provenance_id),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    due_at TEXT,
    superseded_by TEXT REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    scope TEXT,
    provenance_id TEXT REFERENCES provenance(provenance_id),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    superseded_by TEXT REFERENCES decisions(decision_id)
);

CREATE TABLE IF NOT EXISTS preferences (
    preference_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    preference TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    provenance_id TEXT REFERENCES provenance(provenance_id),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    superseded_by TEXT REFERENCES preferences(preference_id)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    subject_type TEXT,
    subject_id TEXT,
    payload_json TEXT NOT NULL,
    provenance_id TEXT REFERENCES provenance(provenance_id),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    metric_id TEXT PRIMARY KEY,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    unit TEXT,
    dimensions_json TEXT,
    provenance_id TEXT REFERENCES provenance(provenance_id),
    measured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_freshness (
    source_ref TEXT PRIMARY KEY,
    last_checked_at TEXT NOT NULL,
    source_timestamp TEXT,
    freshness_status TEXT NOT NULL,
    check_result TEXT,
    provenance_id TEXT REFERENCES provenance(provenance_id)
);

CREATE TABLE IF NOT EXISTS validation_results (
    validation_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    validator TEXT NOT NULL,
    result TEXT NOT NULL,
    evidence_json TEXT,
    validated_at TEXT NOT NULL,
    provenance_id TEXT REFERENCES provenance(provenance_id)
);

CREATE TABLE IF NOT EXISTS version_history (
    version_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    operation TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    provenance_id TEXT REFERENCES provenance(provenance_id),
    UNIQUE(subject_type, subject_id, version_number)
);

CREATE TABLE IF NOT EXISTS run_metrics (
    run_id TEXT PRIMARY KEY,
    request_type TEXT,
    route_selected TEXT,
    tools_json TEXT,
    model_or_agent TEXT,
    input_size INTEGER,
    handoff_size INTEGER,
    duration_ms INTEGER,
    resource_usage_json TEXT,
    errors_json TEXT,
    retries INTEGER DEFAULT 0,
    verification_result TEXT,
    user_correction TEXT,
    final_outcome TEXT,
    acceptance_status TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_events_subject ON events(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_validation_subject ON validation_results(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_run_metrics_started ON run_metrics(started_at);
