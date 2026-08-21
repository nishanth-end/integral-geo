"""
Export script for full NHAI Karnataka projects data table.
Reads all 100 projects from SQLite database (nhai_karnataka.db),
resolves contractors via junction table, computes plain-language
geocode status, and exports to frontend/data/all_projects.json.
"""

import json
import sqlite3
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "nhai_karnataka.db"
OUTPUT_PATH = BASE_DIR / "frontend" / "data" / "all_projects.json"
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)


def export_full_table(db_path: Path = None):
    if db_path is None:
        db_path = DB_PATH

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get contractors map by project_id
    cursor.execute("""
        SELECT pc.project_id, c.name
        FROM project_contractors pc
        JOIN contractors c ON pc.contractor_id = c.id
        ORDER BY pc.project_id, c.name
    """)
    contractors_by_project = defaultdict(list)
    for row in cursor.fetchall():
        contractors_by_project[row['project_id']].append(row['name'])

    # Query all projects
    cursor.execute("""
        SELECT id, project_key, corridor_name, nh_number,
               chainage_start_km, chainage_end_km, package_label,
               lanes_min, lanes_max, has_paved_shoulder, contractor_raw,
               state_raw, source_document, lat_start, lng_start,
               lat_end, lng_end, geocode_method, geocode_confidence
        FROM projects
        ORDER BY id
    """)
    rows = cursor.fetchall()

    projects_list = []
    status_counts = defaultdict(int)

    for r in rows:
        p_id = r['id']
        conf = r['geocode_confidence']
        method = r['geocode_method']
        nh = r['nh_number']
        ch_s = r['chainage_start_km']
        ch_e = r['chainage_end_km']
        lat_s = r['lat_start']
        lng_s = r['lng_start']
        lat_e = r['lat_end']
        lng_e = r['lng_end']

        # Determine geocode status
        if conf == 'approximate' and lat_s is not None and lng_s is not None:
            geocode_status = "Mapped"
            status_category = "Mapped"
        elif method == 'flagged_old_nh_numbering':
            geocode_status = f"Unplaced: Pre-2010 NH Numbering ({nh})"
            status_category = "Unplaced"
        elif not nh:
            geocode_status = "Unplaced: Missing NH Number"
            status_category = "Unplaced"
        elif ch_s is None:
            geocode_status = "Unplaced: Missing Chainage"
            status_category = "Unplaced"
        else:
            geocode_status = "Unplaced: Geocoding Failed"
            status_category = "Unplaced"

        status_counts[geocode_status] += 1

        resolved_conts = contractors_by_project.get(p_id, [])
        if not resolved_conts and r['contractor_raw']:
            resolved_conts = [r['contractor_raw']]

        projects_list.append({
            "id": p_id,
            "project_key": r['project_key'],
            "nh_number": nh or "NO-NH",
            "corridor_name": r['corridor_name'],
            "chainage_start_km": ch_s,
            "chainage_end_km": ch_e,
            "package_label": r['package_label'] or "N/A",
            "lanes_min": r['lanes_min'],
            "lanes_max": r['lanes_max'],
            "has_paved_shoulder": bool(r['has_paved_shoulder']),
            "contractor_raw": r['contractor_raw'] or "Not listed",
            "contractors": resolved_conts,
            "contractors_display": ", ".join(resolved_conts) if resolved_conts else "Not listed",
            "state_raw": r['state_raw'] or "Karnataka",
            "source_document": r['source_document'],
            "lat_start": lat_s,
            "lng_start": lng_s,
            "lat_end": lat_e,
            "lng_end": lng_e,
            "geocode_method": method,
            "geocode_confidence": conf,
            "geocode_status": geocode_status,
            "status_category": status_category
        })

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(projects_list, f, indent=2)

    conn.close()

    print("=" * 80)
    print("STAGE 5 FULL TABLE EXPORT SUMMARY (scripts/export_full_table.py)")
    print("=" * 80)
    print(f"Total Projects Exported: {len(projects_list)}")
    print("-" * 80)
    print("BREAKDOWN BY GEOCODE STATUS:")
    print("-" * 80)
    for status, count in sorted(status_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {status:45s}: {count:3d} projects")
    print("-" * 80)
    print(f"Output File: {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes)")
    print("=" * 80)


if __name__ == '__main__':
    export_full_table()
