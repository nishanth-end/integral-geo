"""
Builds data/nhai_karnataka_v3.db from schema/schema.sql.
Loads all 4 source document JSON datasets into snapshot & canonical model:
1. data/processed/karnataka_parsed.json (IMPL)
2. data/processed/karnataka_completed_pcc_parsed.json (PCC)
3. data/processed/karnataka_awarded_not_appointed_parsed.json (AWD)
4. data/processed/karnataka_balance_for_award_parsed.json (BAL)

Preserves highway_geometry cache from data/nhai_karnataka.db without Overpass re-fetching.
"""

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OLD_DB_PATH = BASE_DIR / "data" / "nhai_karnataka.db"
NEW_DB_PATH = BASE_DIR / "data" / "nhai_karnataka_v3.db"
SCHEMA_PATH = BASE_DIR / "schema" / "schema.sql"
GEOM_BACKUP_PATH = BASE_DIR / "data" / "highway_geometry_backup.json"

FILES = [
    {
        "path": BASE_DIR / "data" / "processed" / "karnataka_parsed.json",
        "prefix": "IMPL",
        "doc_name": "Under_implementation_MIS_august.pdf",
        "status": "Under Implementation",
        "entity_field": "contractor",
        "entity_role": "contractor"
    },
    {
        "path": BASE_DIR / "data" / "processed" / "karnataka_completed_pcc_parsed.json",
        "prefix": "PCC",
        "doc_name": "MIS_august_O_AND_M_PCC.pdf",
        "status": "Completed & PCC / PCOD Issued",
        "entity_field": "concessionaire",
        "entity_role": "concessionaire"
    },
    {
        "path": BASE_DIR / "data" / "processed" / "karnataka_awarded_not_appointed_parsed.json",
        "prefix": "AWD",
        "doc_name": "MIS_Aug_Awarded_not_appointed.pdf",
        "status": "Awarded But Not Started",
        "entity_field": "concessionaire",
        "entity_role": "concessionaire"
    },
    {
        "path": BASE_DIR / "data" / "processed" / "karnataka_balance_for_award_parsed.json",
        "prefix": "BAL",
        "doc_name": "Balance_for_award_MIS_august.pdf",
        "status": "Balance For Award",
        "entity_field": "dpr_name",
        "entity_role": "dpr_consultant"
    }
]

SNAPSHOT_DATE = "2026-08-01"


def clean_entity_name(name: str) -> str:
    """Clean whitespace, trailing punctuation, prefixes, and JV tags from entity name."""
    if not name:
        return ""
    name = name.strip()
    name = re.sub(r'\s*\(\s*(?:JV|Joint\s+Venture|Consortium)\s*\)\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'^(?:M/s|M/S|M/s\.|M/S\.)\s*', '', name)
    name = re.sub(r'[\s,.;-]+$', '', name)
    name = re.sub(r'^[\s,.;-]+', '', name)
    return name.strip()


def split_entities(raw_str: str) -> list:
    """Split raw entity string (contractor / concessionaire / DPR) into individual names."""
    if not raw_str:
        return []
    s = raw_str.strip()
    s = re.sub(r'[,;]+$', '', s).strip()

    if re.search(r'\bin JV with\b', s, re.IGNORECASE):
        parts = re.split(r'\bin JV with\b', s, flags=re.IGNORECASE)
        return [clean_entity_name(p) for p in parts if clean_entity_name(p)]

    jv_marker = False
    jv_pattern = r'\s*\(\s*(?:JV|Joint\s+Venture|Consortium)\s*\)\s*$'
    if re.search(jv_pattern, s, re.IGNORECASE):
        jv_marker = True
        s = re.sub(jv_pattern, '', s, flags=re.IGNORECASE).strip()

    if ' - ' in s:
        parts = s.split(' - ')
        return [clean_entity_name(p) for p in parts if clean_entity_name(p)]

    m_hyphen = re.match(r'^(.*?Ltd\.?|.*?Limited)\s*-\s*(.*)$', s, re.IGNORECASE)
    if m_hyphen:
        return [clean_entity_name(m_hyphen.group(1)), clean_entity_name(m_hyphen.group(2))]

    m_ms = re.match(r'^(.*?Ltd\.?|.*?Limited)\s+(?:M/S|M/s)\s+(.*)$', s, re.IGNORECASE)
    if m_ms:
        return [clean_entity_name(m_ms.group(1)), clean_entity_name(m_ms.group(2))]

    if jv_marker:
        m_ltd = re.match(r'^(.*?Ltd\.?|.*?Limited)\s+([A-Z].*)$', s)
        if m_ltd:
            return [clean_entity_name(m_ltd.group(1)), clean_entity_name(m_ltd.group(2))]

    cleaned = clean_entity_name(s)
    return [cleaned] if cleaned else []


def step1_preserve_highway_geometry():
    print("STEP 1: Preserving highway_geometry cache from existing DB...")
    if not OLD_DB_PATH.exists():
        print(f"  Warning: {OLD_DB_PATH} not found!")
        return []

    old_conn = sqlite3.connect(OLD_DB_PATH)
    old_cur = old_conn.cursor()
    old_cur.execute("SELECT nh_number, geometry_json, total_length_km, updated_at FROM highway_geometry")
    rows = old_cur.fetchall()
    old_conn.close()

    geom_data = []
    for nh, geom_json, length_km, updated_at in rows:
        geom_data.append({
            "nh_number": nh,
            "geometry_json": geom_json,
            "total_length_km": length_km,
            "fetched_at": updated_at
        })

    with open(GEOM_BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(geom_data, f, indent=2)

    print(f"  Preserved {len(geom_data)} highway_geometry records to {GEOM_BACKUP_PATH}")
    return geom_data


def step2_create_new_database():
    print("\nSTEP 2: Initializing fresh data/nhai_karnataka_v3.db from schema/schema.sql...")
    if NEW_DB_PATH.exists():
        os.remove(NEW_DB_PATH)

    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    new_conn = sqlite3.connect(NEW_DB_PATH)
    new_conn.execute("PRAGMA foreign_keys = ON;")
    new_conn.executescript(schema_sql)
    new_conn.commit()
    new_conn.close()
    print(f"  Fresh database created at {NEW_DB_PATH}")


def step3_restore_highway_geometry(geom_data):
    print("\nSTEP 3: Re-inserting preserved highway_geometry rows...")
    conn = sqlite3.connect(NEW_DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()

    for item in geom_data:
        cur.execute(
            """
            INSERT INTO highway_geometry (nh_number, geometry_json, total_length_km, fetched_at)
            VALUES (?, ?, ?, ?)
            """,
            (item["nh_number"], item["geometry_json"], item["total_length_km"], item["fetched_at"])
        )

    conn.commit()
    cur.execute("SELECT COUNT(*) FROM highway_geometry")
    count = cur.fetchone()[0]
    conn.close()
    print(f"  Restored {count} highway_geometry rows into {NEW_DB_PATH}")


def step4_load_snapshots_and_canonical():
    print("\nSTEP 4: Loading all 4 source documents into snapshots, canonical, and junction tables...")
    conn = sqlite3.connect(NEW_DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()

    entity_id_map = {}  # (normalized_name, entity_type) -> entity_id
    ingested_timestamp = datetime.now().isoformat()

    total_snapshots_loaded = 0

    for doc in FILES:
        path = doc["path"]
        prefix = doc["prefix"]
        doc_name = doc["doc_name"]
        status = doc["status"]
        entity_field = doc["entity_field"]
        entity_role = doc["entity_role"]

        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)

        print(f"\n  Processing {prefix} ({len(records)} records from {path.name})...")

        for idx, r in enumerate(records):
            sr_no = r.get("sr_no", idx + 1)
            snapshot_key = f"{prefix}-{sr_no}-{SNAPSHOT_DATE}"
            canonical_project_id = f"CAN-{prefix}-{sr_no}"

            project_name_raw = r.get("project_name_raw", r.get("corridor_name"))
            corridor_name = r.get("corridor_name")
            total_length_km = r.get("total_length_km")
            chainage_start_km = r.get("chainage_start_km")
            chainage_end_km = r.get("chainage_end_km")
            loa_date = r.get("loa_date_civil_work")
            appointed_date = r.get("appointed_date_contractor")
            awarded_cost = r.get("total_awarded_cost_cr")
            mode_raw = r.get("mode")
            is_concession = 1 if r.get("is_concession") else 0
            lanes_raw = r.get("lanes_raw")
            lanes_min = r.get("lanes_min")
            lanes_max = r.get("lanes_max")
            has_paved_shoulder = 1 if r.get("has_paved_shoulder") else 0
            has_multi_loc = 1 if r.get("has_multiple_locations") else 0
            loc_count = r.get("location_mentions_count", 0)

            # 1. Insert project_snapshots row first (with canonical_project_id = NULL initially to avoid circular FK)
            cur.execute(
                """
                INSERT INTO project_snapshots (
                    snapshot_key, sr_no_source, source_document, snapshot_date,
                    status, canonical_project_id, project_name_raw, corridor_name,
                    total_length_km, chainage_start_km, chainage_end_km,
                    loa_date_civil_work, appointed_date_contractor, total_awarded_cost_cr,
                    mode_raw, is_concession, lanes_raw, lanes_min, lanes_max,
                    has_paved_shoulder, has_multiple_locations, location_mentions_count,
                    ingested_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_key, sr_no, doc_name, SNAPSHOT_DATE,
                    status, project_name_raw, corridor_name,
                    total_length_km, chainage_start_km, chainage_end_km,
                    loa_date, appointed_date, awarded_cost,
                    mode_raw, is_concession, lanes_raw, lanes_min, lanes_max,
                    has_paved_shoulder, has_multi_loc, loc_count,
                    ingested_timestamp
                )
            )
            total_snapshots_loaded += 1

            # 2. Insert canonical_projects row referencing the snapshot
            cur.execute(
                """
                INSERT INTO canonical_projects (
                    canonical_project_id, current_status, current_corridor_name,
                    current_snapshot_key, first_seen_date, last_seen_date
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (canonical_project_id, status, corridor_name, snapshot_key, SNAPSHOT_DATE, SNAPSHOT_DATE)
            )

            # 3. Update project_snapshots to point to canonical_project_id
            cur.execute(
                """
                UPDATE project_snapshots
                SET canonical_project_id = ?
                WHERE snapshot_key = ?
                """,
                (canonical_project_id, snapshot_key)
            )

            # 4. Insert project_status_history row
            cur.execute(
                """
                INSERT INTO project_status_history (
                    canonical_project_id, status, source_document, snapshot_date
                ) VALUES (?, ?, ?, ?)
                """,
                (canonical_project_id, status, doc_name, SNAPSHOT_DATE)
            )

            # 5. Insert entities & project_entities
            raw_entity_str = r.get(entity_field)
            if raw_entity_str:
                entity_names = split_entities(raw_entity_str)
                for ent_name in entity_names:
                    norm_name = re.sub(r'\s+', ' ', ent_name.lower()).strip()
                    cache_key = (norm_name, entity_role)
                    if cache_key not in entity_id_map:
                        cur.execute(
                            "INSERT INTO entities (name, normalized_name, entity_type) VALUES (?, ?, ?)",
                            (ent_name, norm_name, entity_role)
                        )
                        ent_id = cur.lastrowid
                        entity_id_map[cache_key] = ent_id
                    else:
                        ent_id = entity_id_map[cache_key]

                    cur.execute(
                        """
                        INSERT OR IGNORE INTO project_entities (snapshot_key, entity_id, role)
                        VALUES (?, ?, ?)
                        """,
                        (snapshot_key, ent_id, entity_role)
                    )

            # 6. Insert project_nh_numbers
            nh_list = []
            if "nh_numbers" in r and r["nh_numbers"]:
                nh_list = r["nh_numbers"]
            elif "nh_number" in r and r["nh_number"]:
                nh_list = [r["nh_number"]]

            for nh in nh_list:
                if not nh:
                    continue
                is_sh = 1 if nh.startswith("SH-") or nh.startswith("SH ") else 0
                cur.execute(
                    """
                    INSERT OR IGNORE INTO project_nh_numbers (snapshot_key, nh_number, is_state_highway)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot_key, nh, is_sh)
                )

            # 7. Insert project_states
            state_raw = r.get("state_raw", "")
            if state_raw:
                states = [s.strip() for s in state_raw.split(",") if s.strip()]
                for st in states:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO project_states (snapshot_key, state_name)
                        VALUES (?, ?)
                        """,
                        (snapshot_key, st)
                    )

            # 8. Insert project_quality_flags
            # A) TBD fields
            tbd_fields = r.get("tbd_fields", [])
            for tf in tbd_fields:
                cur.execute(
                    """
                    INSERT INTO project_quality_flags (snapshot_key, flag_type, detail)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot_key, "tbd_field", f"Field '{tf}' is TBD / unassigned placeholder")
                )

            # B) NH note / discrepancy / scientific notation
            nh_note = r.get("nh_note")
            has_disc = r.get("has_nh_discrepancy")
            if has_disc and nh_note:
                cur.execute(
                    """
                    INSERT INTO project_quality_flags (snapshot_key, flag_type, detail)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot_key, "nh_discrepancy", nh_note)
                )
            elif nh_note and nh_note.startswith("corrupted_scientific_notation"):
                cur.execute(
                    """
                    INSERT INTO project_quality_flags (snapshot_key, flag_type, detail)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot_key, "corrupted_scientific_notation", nh_note)
                )
            elif nh_note and nh_note == "tbd_not_designated" and "nh_number" not in tbd_fields:
                cur.execute(
                    """
                    INSERT INTO project_quality_flags (snapshot_key, flag_type, detail)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot_key, "tbd_field", "Field 'nh_number' is TBD / unassigned placeholder")
                )

            # C) Column bleed warning
            if r.get("has_length_column_bleed_warning"):
                cur.execute(
                    """
                    INSERT INTO project_quality_flags (snapshot_key, flag_type, detail)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot_key, "length_bleed_warning", r.get("length_bleed_note"))
                )

            # D) Multi-location warning
            if r.get("has_multiple_locations"):
                cnt = r.get("location_mentions_count", 2)
                cur.execute(
                    """
                    INSERT INTO project_quality_flags (snapshot_key, flag_type, detail)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot_key, "multiple_locations", f"Bundles {cnt} distinct location/chainage references in project text")
                )

    conn.commit()
    conn.close()
    print(f"\nAll snapshots and canonical structures committed successfully ({total_snapshots_loaded} total).")


def step5_verify_and_print_summary(geom_backup_count):
    print("\n" + "=" * 80)
    print("DATABASE REBUILD VERIFICATION SUMMARY (data/nhai_karnataka_v3.db)")
    print("=" * 80)

    conn = sqlite3.connect(NEW_DB_PATH)
    cur = conn.cursor()

    tables = [
        "project_snapshots",
        "canonical_projects",
        "project_status_history",
        "match_review_queue",
        "entities",
        "entity_aliases",
        "modes",
        "project_entities",
        "project_nh_numbers",
        "project_states",
        "project_quality_flags",
        "highway_geometry",
        "engineers"
    ]

    print("TABLE ROW COUNTS:")
    print("-" * 80)
    counts = {}
    for tbl in tables:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        c = cur.fetchone()[0]
        counts[tbl] = c
        print(f"  - {tbl:28s}: {c:5d} rows")

    print("-" * 80)
    print("CORE RELATION INTEGRITY CHECKS:")
    print("-" * 80)
    snap_count = counts["project_snapshots"]
    can_count = counts["canonical_projects"]
    hist_count = counts["project_status_history"]
    geom_count = counts["highway_geometry"]

    expected_total = 100 + 64 + 10 + 55  # 229

    print(f"  1. project_snapshots count == expected sum (100 + 64 + 10 + 55 = 229):")
    print(f"     -> {snap_count} == {expected_total} [{'PASS' if snap_count == expected_total else 'FAIL'}]")

    print(f"  2. canonical_projects count == 229 (1:1 mapping for initial load):")
    print(f"     -> {can_count} == {expected_total} [{'PASS' if can_count == expected_total else 'FAIL'}]")

    print(f"  3. project_status_history seed rows == 229:")
    print(f"     -> {hist_count} == {expected_total} [{'PASS' if hist_count == expected_total else 'FAIL'}]")

    print(f"  4. highway_geometry preserved count == backup count ({geom_backup_count}):")
    print(f"     -> {geom_count} == {geom_backup_count} [{'PASS' if geom_count == geom_backup_count else 'FAIL'}]")

    print(f"  5. match_review_queue is empty (0 rows):")
    print(f"     -> {counts['match_review_queue']} rows [{'PASS' if counts['match_review_queue'] == 0 else 'FAIL'}]")

    print("-" * 80)
    print("JUNCTION TABLE ORPHAN CHECKS (Foreign Key Integrity):")
    print("-" * 80)
    junction_checks = [
        ("project_entities", "snapshot_key", "project_snapshots", "snapshot_key"),
        ("project_nh_numbers", "snapshot_key", "project_snapshots", "snapshot_key"),
        ("project_states", "snapshot_key", "project_snapshots", "snapshot_key"),
        ("project_quality_flags", "snapshot_key", "project_snapshots", "snapshot_key"),
        ("canonical_projects", "current_snapshot_key", "project_snapshots", "snapshot_key"),
        ("project_snapshots", "canonical_project_id", "canonical_projects", "canonical_project_id"),
        ("project_status_history", "canonical_project_id", "canonical_projects", "canonical_project_id")
    ]

    for j_tbl, j_col, r_tbl, r_col in junction_checks:
        cur.execute(f"""
            SELECT COUNT(*) FROM {j_tbl} j
            LEFT JOIN {r_tbl} r ON j.{j_col} = r.{r_col}
            WHERE r.{r_col} IS NULL
        """)
        orphans = cur.fetchone()[0]
        print(f"  - {j_tbl}.{j_col} -> {r_tbl}.{r_col}: {orphans} orphans [{'PASS' if orphans == 0 else 'FAIL'}]")

    print("-" * 80)
    print("MODES TABLE & SNAPSHOT MODE_RAW INTEGRITY CHECK:")
    print("-" * 80)
    cur.execute("SELECT name, is_concession_default FROM modes")
    modes_in_db = cur.fetchall()
    print("  Modes currently defined in DB:")
    for m_name, is_conc in modes_in_db:
        print(f"    - {m_name:20s} (is_concession_default={is_conc})")

    cur.execute("""
        SELECT DISTINCT mode_raw FROM project_snapshots
        WHERE mode_raw IS NOT NULL
          AND mode_raw NOT IN (SELECT name FROM modes)
    """)
    unaccounted_modes = cur.fetchall()
    print("\n  Unaccounted mode_raw values in project_snapshots:")
    if unaccounted_modes:
        for (um,) in unaccounted_modes:
            print(f"    - UNACCOUNTED: {repr(um)} [FAIL]")
    else:
        print("    - None found (All snapshot mode_raw values exist in modes table) [PASS]")

    print("-" * 80)
    print("DISTINCT ENTITY BREAKDOWN BY ROLE:")
    print("-" * 80)
    cur.execute("""
        SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type ORDER BY entity_type
    """)
    for e_type, cnt in cur.fetchall():
        print(f"  - {e_type:20s}: {cnt:4d} entities")

    cur.execute("""
        SELECT flag_type, COUNT(*) FROM project_quality_flags GROUP BY flag_type ORDER BY COUNT(*) DESC
    """)
    print("\nPROJECT QUALITY FLAGS SUMMARY:")
    print("-" * 80)
    for f_type, cnt in cur.fetchall():
        print(f"  - {f_type:30s}: {cnt:4d} flags")

    print("=" * 80)
    conn.close()


if __name__ == '__main__':
    geom_data = step1_preserve_highway_geometry()
    step2_create_new_database()
    step3_restore_highway_geometry(geom_data)
    step4_load_snapshots_and_canonical()
    step5_verify_and_print_summary(len(geom_data))
