-- NHAI Karnataka Road Project Tracker
-- Database Schema for SQLite

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT UNIQUE,
    corridor_name TEXT,
    nh_number TEXT,
    chainage_start_km REAL,
    chainage_end_km REAL,
    package_label TEXT,
    lanes_min INTEGER,
    lanes_max INTEGER,
    has_paved_shoulder BOOLEAN DEFAULT 0,
    contractor_raw TEXT,
    state_raw TEXT,
    source_document TEXT,
    lat_start REAL,
    lng_start REAL,
    lat_end REAL,
    lng_end REAL,
    geocode_method TEXT,
    geocode_confidence TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contractors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contractor_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contractor_id INTEGER NOT NULL,
    alias TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (contractor_id) REFERENCES contractors(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_contractors (
    project_id INTEGER NOT NULL,
    contractor_id INTEGER NOT NULL,
    role TEXT DEFAULT 'Contractor',
    PRIMARY KEY (project_id, contractor_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (contractor_id) REFERENCES contractors(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_states (
    project_id INTEGER NOT NULL,
    state_name TEXT NOT NULL,
    PRIMARY KEY (project_id, state_name),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Highway Geometry Cache Table
CREATE TABLE IF NOT EXISTS highway_geometry (
    nh_number TEXT PRIMARY KEY,
    geometry_json TEXT NOT NULL,
    point_count INTEGER NOT NULL,
    total_length_km REAL NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for efficient queries and joins
CREATE INDEX IF NOT EXISTS idx_projects_nh_number ON projects(nh_number);
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_project_key ON projects(project_key);
CREATE INDEX IF NOT EXISTS idx_project_contractors_proj ON project_contractors(project_id);
CREATE INDEX IF NOT EXISTS idx_project_contractors_cont ON project_contractors(contractor_id);
CREATE INDEX IF NOT EXISTS idx_project_states_proj ON project_states(project_id);
CREATE INDEX IF NOT EXISTS idx_project_states_state ON project_states(state_name);
