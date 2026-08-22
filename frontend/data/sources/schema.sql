-- ============================================================================
-- NHAI Karnataka Road Accountability Tracker — Schema v3
-- Snapshot / Canonical split, designed for recurring monthly re-ingestion
-- and eventual nationwide scope. See NHAI_Tracker_Schema_v3.docx for the
-- full design rationale behind each table.
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- LAYER 1: project_snapshots — immutable record of what one source document
-- said about one project row on one date. Never edited or merged after insert.
-- ----------------------------------------------------------------------------
CREATE TABLE project_snapshots (
    snapshot_key            TEXT PRIMARY KEY,   -- {DOC_CODE}-{sr_no}-{snapshot_date}
    sr_no_source             INTEGER NOT NULL,
    source_document          TEXT NOT NULL,
    snapshot_date             DATE NOT NULL,
    status                   TEXT NOT NULL,       -- Under Implementation | Completed & PCC/PCOD Issued
                                                    -- | Awarded But Not Started | Balance For Award
    canonical_project_id      TEXT,                 -- nullable until matched
    project_name_raw          TEXT,
    corridor_name             TEXT,
    total_length_km           REAL,
    chainage_start_km         REAL,
    chainage_end_km           REAL,
    loa_date_civil_work       TEXT,
    appointed_date_contractor TEXT,
    total_awarded_cost_cr     REAL,
    mode_raw                  TEXT REFERENCES modes(name),
    is_concession              BOOLEAN,
    lanes_raw                 TEXT,
    lanes_min                 INTEGER,
    lanes_max                 INTEGER,
    has_paved_shoulder         BOOLEAN,
    has_multiple_locations     BOOLEAN DEFAULT 0,
    location_mentions_count    INTEGER DEFAULT 0,
    ingested_at                TEXT NOT NULL,
    FOREIGN KEY (canonical_project_id) REFERENCES canonical_projects(canonical_project_id)
);

CREATE INDEX idx_snapshots_canonical ON project_snapshots(canonical_project_id);
CREATE INDEX idx_snapshots_date ON project_snapshots(snapshot_date);

-- ----------------------------------------------------------------------------
-- LAYER 2: canonical_projects — one row per real-world physical highway
-- project. What the public map/site actually queries.
-- ----------------------------------------------------------------------------
CREATE TABLE canonical_projects (
    canonical_project_id     TEXT PRIMARY KEY,
    current_status            TEXT,
    current_corridor_name     TEXT,
    current_snapshot_key      TEXT REFERENCES project_snapshots(snapshot_key),
    first_seen_date           DATE,
    last_seen_date            DATE
);

-- ----------------------------------------------------------------------------
-- project_status_history — byproduct of linking snapshots to canonical
-- projects. Tracks how long a project sits in each pipeline stage.
-- ----------------------------------------------------------------------------
CREATE TABLE project_status_history (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_project_id      TEXT NOT NULL REFERENCES canonical_projects(canonical_project_id),
    status                    TEXT NOT NULL,
    source_document            TEXT NOT NULL,
    snapshot_date              DATE NOT NULL
);

CREATE INDEX idx_status_history_project ON project_status_history(canonical_project_id);

-- ----------------------------------------------------------------------------
-- match_review_queue — medium-confidence snapshot-to-canonical matches
-- awaiting manual confirmation. Empty for this initial merge (no ambiguity
-- yet — all four documents share one snapshot date).
-- ----------------------------------------------------------------------------
CREATE TABLE match_review_queue (
    review_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_key               TEXT NOT NULL REFERENCES project_snapshots(snapshot_key),
    candidate_canonical_project_id TEXT REFERENCES canonical_projects(canonical_project_id),
    match_reason                TEXT,
    resolution                  TEXT DEFAULT 'pending'   -- 'confirmed' | 'rejected' | 'pending'
);

-- ----------------------------------------------------------------------------
-- entities — contractors, concessionaires, and DPR consultants in one table,
-- distinguished by role on the junction rather than by separate tables.
-- ----------------------------------------------------------------------------
CREATE TABLE entities (
    entity_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name                       TEXT NOT NULL,
    normalized_name             TEXT NOT NULL,   -- lowercased, whitespace-collapsed
    entity_type                 TEXT NOT NULL     -- 'contractor' | 'concessionaire' | 'dpr_consultant'
);

CREATE INDEX idx_entities_normalized ON entities(normalized_name);

CREATE TABLE entity_aliases (
    alias_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id                  INTEGER NOT NULL REFERENCES entities(entity_id),
    alias_name                  TEXT NOT NULL
);

-- ----------------------------------------------------------------------------
-- modes — insert-only lookup, not a hard enum/CHECK constraint, since new
-- Mode values have appeared in every new source document ingested so far.
-- ----------------------------------------------------------------------------
CREATE TABLE modes (
    name                      TEXT PRIMARY KEY,     -- 'EPC', 'HAM', 'BOT Toll', 'TBD', etc.
    is_concession_default      BOOLEAN DEFAULT 0
);

INSERT INTO modes (name, is_concession_default) VALUES
    ('EPC', 0), ('HAM', 1), ('BOT Toll', 1), ('BOT Annuity', 1),
    ('Item Rate', 0), ('PBMC', 0), ('InvIT', 0), ('OMT', 0),
    ('TOT', 1), ('Not Applicable', 0), ('TBD', 0);

-- ----------------------------------------------------------------------------
-- project_entities — junction. Handles JV splits and an entity holding more
-- than one role across the dataset. Linked to snapshots (the source-of-truth
-- record); canonical-level "current" entities are derived via
-- canonical_projects.current_snapshot_key.
-- ----------------------------------------------------------------------------
CREATE TABLE project_entities (
    snapshot_key               TEXT NOT NULL REFERENCES project_snapshots(snapshot_key),
    entity_id                   INTEGER NOT NULL REFERENCES entities(entity_id),
    role                         TEXT NOT NULL,   -- 'contractor' | 'concessionaire' | 'dpr_consultant'
    PRIMARY KEY (snapshot_key, entity_id, role)
);

-- ----------------------------------------------------------------------------
-- project_nh_numbers — junction. Handles multi-highway projects, including
-- State Highway segments bundled into an NHAI project package.
-- ----------------------------------------------------------------------------
CREATE TABLE project_nh_numbers (
    snapshot_key               TEXT NOT NULL REFERENCES project_snapshots(snapshot_key),
    nh_number                    TEXT NOT NULL,
    is_state_highway              BOOLEAN DEFAULT 0,
    PRIMARY KEY (snapshot_key, nh_number)
);

-- ----------------------------------------------------------------------------
-- project_states — junction. Multi-state projects; also the hard filter
-- required before any canonical matching runs.
-- ----------------------------------------------------------------------------
CREATE TABLE project_states (
    snapshot_key               TEXT NOT NULL REFERENCES project_snapshots(snapshot_key),
    state_name                   TEXT NOT NULL,
    PRIMARY KEY (snapshot_key, state_name)
);

-- ----------------------------------------------------------------------------
-- project_quality_flags — open-ended data-quality flags per snapshot.
-- New flag types are new rows, never new columns.
-- ----------------------------------------------------------------------------
CREATE TABLE project_quality_flags (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_key                TEXT NOT NULL REFERENCES project_snapshots(snapshot_key),
    flag_type                    TEXT NOT NULL,   -- 'tbd_field' | 'nh_discrepancy' |
                                                     -- 'length_bleed_warning' | 'multiple_locations' | ...
    detail                       TEXT
);

CREATE INDEX idx_quality_flags_snapshot ON project_quality_flags(snapshot_key);

-- ----------------------------------------------------------------------------
-- highway_geometry — cached OSM geometry per NH number (Stage 2). Carried
-- forward unchanged; preserve existing rows from the old DB on migration to
-- avoid re-fetching Overpass data for highways already known.
-- ----------------------------------------------------------------------------
CREATE TABLE highway_geometry (
    nh_number                  TEXT PRIMARY KEY,
    geometry_json                TEXT NOT NULL,   -- stitched OSM way coordinates
    total_length_km               REAL,
    fetched_at                    TEXT
);

-- ----------------------------------------------------------------------------
-- engineers — reserved for RTI-sourced engineer/chief-engineer data.
-- ----------------------------------------------------------------------------
CREATE TABLE engineers (
    engineer_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name                        TEXT,
    designation                  TEXT,
    jurisdiction                  TEXT
);
