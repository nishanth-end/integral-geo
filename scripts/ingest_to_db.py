"""
Database ingestion script for NHAI Karnataka road projects.
Reads parsed JSON data and populates SQLite database according to schema.sql.
Handles joint-venture contractor strings as many-to-many relationships.
"""

import json
import os
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_JSON_PATH = BASE_DIR / "data" / "processed" / "karnataka_parsed.json"
FALLBACK_JSON_PATH = BASE_DIR / "data" / "karnataka_parsed.json"
DB_PATH = BASE_DIR / "data" / "nhai_karnataka.db"
SCHEMA_PATH = BASE_DIR / "schema" / "schema.sql"


def clean_contractor_name(name: str) -> str:
    """Clean whitespace, trailing punctuation, prefixes, and JV tags from contractor name."""
    if not name:
        return ""
    name = name.strip()
    # Remove trailing JV / Consortium / Joint Venture markers
    name = re.sub(r'\s*\(\s*(?:JV|Joint\s+Venture|Consortium)\s*\)\s*$', '', name, flags=re.IGNORECASE)
    # Remove leading M/s or M/S
    name = re.sub(r'^(?:M/s|M/S|M/s\.|M/S\.)\s*', '', name)
    # Strip trailing and leading punctuation
    name = re.sub(r'[\s,.;-]+$', '', name)
    name = re.sub(r'^[\s,.;-]+', '', name)
    return name.strip()


def split_contractors(raw_str: str) -> list:
    """
    Split raw contractor string into individual contractor entities.
    Handles JV patterns like:
      - 'APS Hydro Private Limited - R&C Infra engineers Private Limited (JV)'
      - 'Bekem Infra projects Pvt. Ltd-M/s DHD Infracon Private Limited ( consortium )'
      - 'Shree Girrajjee Infra Heights Private Limited in JV with M/s Shiva Associates'
      - 'Power Mech Projects Ltd. - SRC Infra Developers Pvt. Ltd.'
      - 'R.N.S. Infrastructure Ltd. Gayatri Projects Ltd (JV)'
    """
    if not raw_str:
        return []
    s = raw_str.strip()
    s = re.sub(r'[,;]+$', '', s).strip()

    # 1. 'in JV with'
    if re.search(r'\bin JV with\b', s, re.IGNORECASE):
        parts = re.split(r'\bin JV with\b', s, flags=re.IGNORECASE)
        return [clean_contractor_name(p) for p in parts if clean_contractor_name(p)]

    # Detect JV marker at end
    jv_marker = False
    jv_pattern = r'\s*\(\s*(?:JV|Joint\s+Venture|Consortium)\s*\)\s*$'
    if re.search(jv_pattern, s, re.IGNORECASE):
        jv_marker = True
        s = re.sub(jv_pattern, '', s, flags=re.IGNORECASE).strip()

    # If ' - ' in s (e.g. 'APS Hydro Private Limited - R&C Infra engineers Private Limited')
    if ' - ' in s:
        parts = s.split(' - ')
        return [clean_contractor_name(p) for p in parts if clean_contractor_name(p)]

    # Hyphen without spaces separating company names
    m_hyphen = re.match(r'^(.*?Ltd\.?|.*?Limited)\s*-\s*(.*)$', s, re.IGNORECASE)
    if m_hyphen:
        return [clean_contractor_name(m_hyphen.group(1)), clean_contractor_name(m_hyphen.group(2))]

    # 'DHD Infracon Private Limited M/S Bekem Infra projects Pvt. Ltd'
    m_ms = re.match(r'^(.*?Ltd\.?|.*?Limited)\s+(?:M/S|M/s)\s+(.*)$', s, re.IGNORECASE)
    if m_ms:
        return [clean_contractor_name(m_ms.group(1)), clean_contractor_name(m_ms.group(2))]

    # 'R.N.S. Infrastructure Ltd. Gayatri Projects Ltd'
    if jv_marker:
        m_ltd = re.match(r'^(.*?Ltd\.?|.*?Limited)\s+([A-Z].*)$', s)
        if m_ltd:
            return [clean_contractor_name(m_ltd.group(1)), clean_contractor_name(m_ltd.group(2))]

    cleaned = clean_contractor_name(s)
    return [cleaned] if cleaned else []


def split_states(state_raw: str) -> list:
    """Split comma-separated state strings into individual cleaned state names."""
    if not state_raw:
        return []
    return [s.strip() for s in state_raw.split(',') if s.strip()]


def init_db(conn: sqlite3.Connection, schema_path: Path):
    """Execute schema DDL to create tables and indexes."""
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema_sql = f.read()
    conn.executescript(schema_sql)


def ingest_data(json_path: Path = None, db_path: Path = None, schema_path: Path = None):
    """Read parsed JSON and load into SQLite database."""
    if json_path is None:
        json_path = DEFAULT_JSON_PATH if DEFAULT_JSON_PATH.exists() else FALLBACK_JSON_PATH
    if db_path is None:
        db_path = DB_PATH
    if schema_path is None:
        schema_path = SCHEMA_PATH

    # Remove existing database file for a clean load
    if db_path.exists():
        os.remove(db_path)

    with open(json_path, 'r', encoding='utf-8') as f:
        records = json.load(f)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    init_db(conn, schema_path)

    cursor = conn.cursor()

    # Cache contractor id by name
    contractor_id_map = {}

    for item in records:
        corridor_name = item.get('corridor_name')
        nh_number = item.get('nh_number')
        chainage_start_km = item.get('chainage_start_km')
        chainage_end_km = item.get('chainage_end_km')
        package_label = item.get('package_label')
        lanes_min = item.get('lanes_min')
        lanes_max = item.get('lanes_max')
        has_paved_shoulder = 1 if item.get('has_paved_shoulder') else 0
        contractor_raw = item.get('contractor')
        state_raw = item.get('state_raw')
        source_document = item.get('source_document', 'Under_implementation_MIS_august.pdf')

        # Insert project
        cursor.execute(
            """
            INSERT INTO projects (
                corridor_name, nh_number, chainage_start_km, chainage_end_km,
                package_label, lanes_min, lanes_max, has_paved_shoulder,
                contractor_raw, state_raw, source_document
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corridor_name, nh_number, chainage_start_km, chainage_end_km,
                package_label, lanes_min, lanes_max, has_paved_shoulder,
                contractor_raw, state_raw, source_document
            )
        )
        project_id = cursor.lastrowid

        # Handle contractors (many-to-many)
        contractor_names = split_contractors(contractor_raw)
        is_jv = len(contractor_names) > 1

        for c_name in contractor_names:
            if c_name not in contractor_id_map:
                cursor.execute(
                    "INSERT OR IGNORE INTO contractors (name) VALUES (?)",
                    (c_name,)
                )
                cursor.execute("SELECT id FROM contractors WHERE name = ?", (c_name,))
                c_id = cursor.fetchone()[0]
                contractor_id_map[c_name] = c_id
            else:
                c_id = contractor_id_map[c_name]

            role = 'JV Partner' if is_jv else 'Contractor'
            cursor.execute(
                """
                INSERT OR IGNORE INTO project_contractors (project_id, contractor_id, role)
                VALUES (?, ?, ?)
                """,
                (project_id, c_id, role)
            )

        # Handle states (many-to-many)
        states = split_states(state_raw)
        for s_name in states:
            cursor.execute(
                """
                INSERT OR IGNORE INTO project_states (project_id, state_name)
                VALUES (?, ?)
                """,
                (project_id, s_name)
            )

    conn.commit()
    conn.close()
    print(f"Ingestion complete: Loaded {len(records)} records into {db_path}")


def print_verification_summary(db_path: Path = None):
    """Query database and print summary stats."""
    if db_path is None:
        db_path = DB_PATH

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Total project count
    cursor.execute("SELECT COUNT(*) FROM projects;")
    total_projects = cursor.fetchone()[0]

    # 2. Total distinct contractors
    cursor.execute("SELECT COUNT(*) FROM contractors;")
    total_contractors = cursor.fetchone()[0]

    # 3. Projects with more than one contractor (JV projects)
    cursor.execute("""
        SELECT 
            p.id,
            p.nh_number,
            p.corridor_name,
            p.contractor_raw,
            GROUP_CONCAT(c.name, ' | ') AS parsed_contractors,
            COUNT(pc.contractor_id) AS contractor_count
        FROM projects p
        JOIN project_contractors pc ON p.id = pc.project_id
        JOIN contractors c ON pc.contractor_id = c.id
        GROUP BY p.id
        HAVING COUNT(pc.contractor_id) > 1
        ORDER BY p.id;
    """)
    jv_projects = cursor.fetchall()

    print("=" * 80)
    print("DATABASE VERIFICATION SUMMARY")
    print("=" * 80)
    print(f"Total Projects:              {total_projects}")
    print(f"Total Distinct Contractors:  {total_contractors}")
    print(f"Multi-Contractor (JV) Count: {len(jv_projects)}")
    print("-" * 80)
    print("PROJECTS WITH MORE THAN ONE CONTRACTOR:")
    print("-" * 80)
    for row in jv_projects:
        proj_id, nh, corridor, raw_contractor, parsed_contractors, count = row
        print(f"Project ID: {proj_id:2d} | NH: {nh or 'N/A'}")
        print(f"  Corridor:           {corridor}")
        print(f"  Raw Contractor:     {raw_contractor}")
        print(f"  Parsed Contractors: {parsed_contractors} ({count} contractors)")
        print()
    print("=" * 80)

    conn.close()


if __name__ == '__main__':
    ingest_data()
    print_verification_summary()
