from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_VERSION = "china-trial-partner-demo-v2"


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback like sqlite3.Connection and then release the file handle."""

    def __exit__(self, exc_type, exc_value, traceback):  # type: ignore[override]
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organizations (
    id INTEGER PRIMARY KEY,
    organization_code TEXT NOT NULL UNIQUE,
    organization_name_zh TEXT NOT NULL,
    organization_name_en TEXT,
    partner_status TEXT NOT NULL,
    country_region TEXT NOT NULL DEFAULT '中国',
    province TEXT,
    city TEXT,
    official_rank INTEGER,
    official_trial_count INTEGER,
    source_url TEXT,
    valid_from TEXT,
    valid_to TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    submission_batch_id TEXT NOT NULL,
    data_as_of_date TEXT NOT NULL,
    template_version TEXT NOT NULL,
    source_file TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_counts_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(organization_id, submission_batch_id)
);

CREATE TABLE IF NOT EXISTS trials (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    import_batch_id INTEGER REFERENCES import_batches(id),
    partner_trial_id TEXT NOT NULL,
    protocol_number TEXT,
    chictr_registration_number TEXT,
    other_registry_ids TEXT,
    primary_registry TEXT,
    utrn TEXT,
    title_zh TEXT NOT NULL,
    title_en TEXT,
    scientific_title TEXT,
    recruitment_status TEXT NOT NULL,
    study_type TEXT,
    study_design TEXT,
    study_phase TEXT,
    primary_disease TEXT NOT NULL,
    disease_aliases TEXT,
    histology TEXT,
    disease_stage TEXT,
    biomarker_summary TEXT,
    intervention_summary TEXT,
    target_enrollment INTEGER,
    target_enrollment_text TEXT,
    actual_enrollment INTEGER,
    sex TEXT,
    minimum_age_years REAL,
    maximum_age_years REAL,
    maximum_ecog REAL,
    first_enrollment_date TEXT,
    planned_end_date TEXT,
    registration_date TEXT,
    last_update_date TEXT,
    countries TEXT,
    primary_sponsor TEXT,
    lead_institution TEXT,
    source_url TEXT,
    last_verified_date TEXT,
    record_status TEXT NOT NULL DEFAULT '有效',
    brief_summary TEXT,
    data_notes TEXT,
    raw_json TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, partner_trial_id)
);

CREATE INDEX IF NOT EXISTS idx_trials_registry ON trials(chictr_registration_number);
CREATE INDEX IF NOT EXISTS idx_trials_status ON trials(record_status, recruitment_status);
CREATE INDEX IF NOT EXISTS idx_trials_registration_date ON trials(registration_date);

CREATE TABLE IF NOT EXISTS trial_registry_ids (
    id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    registry_source TEXT,
    registry_id TEXT NOT NULL,
    id_type TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    source_url TEXT,
    UNIQUE(trial_id, registry_id)
);

CREATE TABLE IF NOT EXISTS trial_sites (
    id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    site_id TEXT NOT NULL,
    site_name TEXT NOT NULL,
    province TEXT,
    city TEXT,
    district TEXT,
    recruitment_status TEXT,
    principal_investigator TEXT,
    contact_channel TEXT,
    address TEXT,
    country TEXT,
    evidence_type TEXT,
    last_verified_date TEXT,
    record_status TEXT NOT NULL DEFAULT '有效',
    UNIQUE(trial_id, site_id)
);

CREATE TABLE IF NOT EXISTS trial_contacts (
    id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    contact_type TEXT,
    contact_name TEXT,
    affiliation TEXT,
    country TEXT,
    province TEXT,
    city TEXT,
    address TEXT,
    telephone TEXT,
    email TEXT,
    record_status TEXT NOT NULL DEFAULT '有效'
);

CREATE TABLE IF NOT EXISTS trial_cohorts (
    id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    cohort_id TEXT NOT NULL,
    cohort_name TEXT NOT NULL,
    cohort_status TEXT,
    disease_scope TEXT,
    biomarker_scope TEXT,
    intervention_name TEXT,
    intervention_type TEXT,
    arm_description TEXT,
    target_enrollment INTEGER,
    record_status TEXT NOT NULL DEFAULT '有效',
    UNIQUE(trial_id, cohort_id)
);

CREATE TABLE IF NOT EXISTS trial_interventions (
    id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    cohort_id TEXT NOT NULL DEFAULT 'ALL',
    intervention_name TEXT NOT NULL,
    intervention_type TEXT,
    target TEXT,
    mechanism TEXT,
    therapy_class TEXT,
    description TEXT,
    record_status TEXT NOT NULL DEFAULT '有效'
);

CREATE TABLE IF NOT EXISTS eligibility_criteria (
    id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    cohort_id TEXT NOT NULL DEFAULT 'ALL',
    criterion_type TEXT NOT NULL CHECK(criterion_type IN ('inclusion', 'exclusion')),
    criterion_id TEXT NOT NULL,
    criterion_order INTEGER NOT NULL,
    criterion_category TEXT NOT NULL,
    criterion_text_zh TEXT NOT NULL,
    criterion_text_en TEXT,
    is_mandatory INTEGER NOT NULL DEFAULT 1,
    structured_field TEXT,
    operator TEXT,
    structured_value TEXT,
    unit TEXT,
    normalization_note TEXT,
    record_status TEXT NOT NULL DEFAULT '有效',
    UNIQUE(trial_id, criterion_type, cohort_id, criterion_id)
);

CREATE INDEX IF NOT EXISTS idx_criteria_trial ON eligibility_criteria(trial_id, criterion_type);

CREATE TABLE IF NOT EXISTS trial_outcomes (
    id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    outcome_type TEXT NOT NULL CHECK(outcome_type IN ('primary', 'secondary')),
    outcome_order INTEGER NOT NULL,
    outcome_text TEXT NOT NULL,
    UNIQUE(trial_id, outcome_type, outcome_order)
);

CREATE TABLE IF NOT EXISTS trial_ethics_reviews (
    id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    review_status TEXT,
    approval_date TEXT,
    committee_name TEXT,
    contact_name TEXT,
    contact_channel TEXT,
    UNIQUE(trial_id, review_status, approval_date, committee_name)
);

CREATE TABLE IF NOT EXISTS trial_support_sources (
    id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    support_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    UNIQUE(trial_id, support_type, source_name)
);
"""


def connect(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    db_path = Path(path).resolve()
    if readonly:
        connection = sqlite3.connect(
            f"file:{db_path.as_posix()}?mode=ro", uri=True, factory=ClosingConnection
        )
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize(path: str | Path) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA_SQL)
        migrations = {
            "trials": {
                "primary_registry": "TEXT", "utrn": "TEXT", "scientific_title": "TEXT", "study_design": "TEXT",
                "target_enrollment_text": "TEXT", "actual_enrollment": "INTEGER", "registration_date": "TEXT",
                "last_update_date": "TEXT", "countries": "TEXT",
            },
            "trial_sites": {"country": "TEXT", "evidence_type": "TEXT"},
        }
        for table, columns in migrations.items():
            existing = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (SCHEMA_VERSION,),
        )


def replace_trial_search(connection: sqlite3.Connection) -> str:
    """Build a local full-text index; prefer trigram for Chinese text."""
    connection.execute("DROP TABLE IF EXISTS trial_search")
    tokenizer = "trigram"
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE trial_search USING fts5(search_text, tokenize='trigram')"
        )
    except sqlite3.OperationalError:
        tokenizer = "unicode61"
        connection.execute(
            "CREATE VIRTUAL TABLE trial_search USING fts5(search_text, tokenize='unicode61')"
        )
    connection.execute(
        """
        INSERT INTO trial_search(rowid, search_text)
        SELECT id, trim(
            coalesce(title_zh, '') || ' ' || coalesce(title_en, '') || ' ' ||
            coalesce(primary_disease, '') || ' ' || coalesce(disease_aliases, '') || ' ' ||
            coalesce(histology, '') || ' ' || coalesce(disease_stage, '') || ' ' ||
            coalesce(biomarker_summary, '') || ' ' || coalesce(intervention_summary, '') || ' ' ||
            coalesce(brief_summary, '')
        )
        FROM trials WHERE record_status='有效'
        """
    )
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES('fts_tokenizer', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (tokenizer,),
    )
    return tokenizer
