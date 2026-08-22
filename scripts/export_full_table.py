"""
Export script for full NHAI Karnataka projects master data table (Schema v3).
Reads all 229 canonical projects from SQLite database (nhai_karnataka_v3.db),
resolves entities with specific roles (Contractor vs Concessionaire vs DPR Consultant),
resolves multi-NH numbers and multi-states, computes plain-language geocode statuses,
and exports to frontend/data/all_projects.json.
"""

import json
import sqlite3
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "nhai_karnataka_v3.db"
OUTPUT_PATH = BASE_DIR / "frontend" / "data" / "all_projects.json"
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

SOURCE_DOC_LABELS = {
    "Under_implementation_MIS_august.pdf": "Under Implementation",
    "MIS_august_O_AND_M_PCC.pdf": "Completed & PCC",
    "MIS_Aug_Awarded_not_appointed.pdf": "Awarded But Not Started",
    "Balance_for_award_MIS_august.pdf": "Balance For Award"
}


def export_full_table(db_path: Path = None):
    if db_path is None:
        db_path = DB_PATH

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Fetch entities mapped by snapshot_key
    cursor.execute("""
        SELECT pe.snapshot_key, pe.role, e.name
        FROM project_entities pe
        JOIN entities e ON pe.entity_id = e.entity_id
        ORDER BY pe.snapshot_key, pe.role, e.name
    """)
    entities_by_snap = defaultdict(list)
    for r in cursor.fetchall():
        entities_by_snap[r['snapshot_key']].append({
            "role": r['role'],
            "name": r['name']
        })

    # 2. Fetch NH numbers mapped by snapshot_key
    cursor.execute("""
        SELECT snapshot_key, nh_number, is_state_highway
        FROM project_nh_numbers
        ORDER BY snapshot_key, nh_number
    """)
    nhs_by_snap = defaultdict(list)
    for r in cursor.fetchall():
        nhs_by_snap[r['snapshot_key']].append(r['nh_number'])

    # 3. Fetch States mapped by snapshot_key
    cursor.execute("""
        SELECT snapshot_key, state_name
        FROM project_states
        ORDER BY snapshot_key, state_name
    """)
    states_by_snap = defaultdict(list)
    for r in cursor.fetchall():
        states_by_snap[r['snapshot_key']].append(r['state_name'])

    # 4. Query all Canonical Projects joined with their current active snapshot
    cursor.execute("""
        SELECT 
            cp.canonical_project_id,
            cp.current_status,
            cp.current_corridor_name,
            cp.current_snapshot_key,
            cp.first_seen_date,
            cp.last_seen_date,
            s.snapshot_key,
            s.sr_no_source,
            s.source_document,
            s.snapshot_date,
            s.status as snapshot_status,
            s.project_name_raw,
            s.corridor_name,
            s.total_length_km,
            s.chainage_start_km,
            s.chainage_end_km,
            s.loa_date_civil_work,
            s.appointed_date_contractor,
            s.total_awarded_cost_cr,
            s.mode_raw,
            s.is_concession,
            s.lanes_raw,
            s.lanes_min,
            s.lanes_max,
            s.has_paved_shoulder,
            s.has_multiple_locations,
            s.location_mentions_count,
            s.lat_start,
            s.lng_start,
            s.lat_end,
            s.lng_end,
            s.geocode_method,
            s.geocode_confidence
        FROM canonical_projects cp
        JOIN project_snapshots s ON cp.current_snapshot_key = s.snapshot_key
        ORDER BY cp.canonical_project_id
    """)
    rows = cursor.fetchall()

    projects_list = []
    status_counts = defaultdict(int)
    doc_counts = defaultdict(int)

    for r in rows:
        row_dict = dict(r)
        snap_key = row_dict['snapshot_key']
        can_id = row_dict['canonical_project_id']
        conf = row_dict.get('geocode_confidence')
        method = row_dict.get('geocode_method')
        ch_s = row_dict.get('chainage_start_km')
        ch_e = row_dict.get('chainage_end_km')
        lat_s = row_dict.get('lat_start')
        lng_s = row_dict.get('lng_start')
        lat_e = row_dict.get('lat_end')
        lng_e = row_dict.get('lng_end')
        doc_raw = row_dict.get('source_document')

        # Resolved NHs and states
        nh_list = nhs_by_snap.get(snap_key, [])
        primary_nh = nh_list[0] if nh_list else "NO-NH"
        state_list = states_by_snap.get(snap_key, [])
        state_str = ", ".join(state_list) if state_list else "Karnataka"

        # Resolved entities with roles
        ents = entities_by_snap.get(snap_key, [])
        if ents:
            roles_present = sorted({e['role'] for e in ents})
            ent_names = [e['name'] for e in ents]
            if len(ent_names) > 1 and "contractor" in roles_present:
                ent_display = f"{', '.join(ent_names)} (JV)"
            elif "concessionaire" in roles_present:
                ent_display = f"{', '.join(ent_names)} (Concessionaire)"
            elif "dpr_consultant" in roles_present:
                ent_display = f"{', '.join(ent_names)} (DPR Consultant)"
            else:
                ent_display = ", ".join(ent_names)
            
            if "contractor" in roles_present:
                role_label = "Contractor (JV)" if len(ent_names) > 1 else "Contractor"
            elif "concessionaire" in roles_present:
                role_label = "Concessionaire"
            elif "dpr_consultant" in roles_present:
                role_label = "DPR Consultant"
            else:
                role_label = roles_present[0].capitalize()
        else:
            ent_names = []
            ent_display = "Not listed"
            role_label = "Not listed"

        # Geocode status determination
        if conf == 'approximate' and lat_s is not None and lng_s is not None:
            geocode_status = "Mapped"
            status_category = "Mapped"
        elif method == 'flagged_old_nh_numbering':
            geocode_status = f"Unplaced: Pre-2010 NH Numbering ({primary_nh})"
            status_category = "Unplaced"
        elif method == 'missing_nh_identifier' or not nh_list or primary_nh == 'NO-NH':
            geocode_status = "Unplaced: Missing NH Number (TBD)"
            status_category = "Unplaced"
        elif method == 'skipped_no_chainage' or ch_s is None:
            geocode_status = "Unplaced: Missing Chainage"
            status_category = "Unplaced"
        elif method == 'nh_not_found_in_osm':
            geocode_status = f"Unplaced: Highway Not in OSM ({primary_nh})"
            status_category = "Unplaced"
        else:
            geocode_status = f"Unplaced: {method or 'Failed'}"
            status_category = "Unplaced"

        status_counts[geocode_status] += 1
        doc_counts[doc_raw] += 1

        projects_list.append({
            "id": row_dict['sr_no_source'],
            "canonical_project_id": can_id,
            "project_key": snap_key,
            "nh_number": primary_nh,
            "nh_numbers": nh_list,
            "corridor_name": row_dict['corridor_name'] or row_dict['current_corridor_name'] or "Untitled Section",
            "chainage_start_km": ch_s,
            "chainage_end_km": ch_e,
            "total_length_km": row_dict['total_length_km'],
            "package_label": row_dict['lanes_raw'] or "N/A",
            "lanes_min": row_dict['lanes_min'],
            "lanes_max": row_dict['lanes_max'],
            "has_paved_shoulder": bool(row_dict['has_paved_shoulder']),
            "contractor_raw": ent_display,
            "contractors": ent_names,
            "contractors_display": ent_display,
            "entity_role": role_label,
            "cost_cr": row_dict['total_awarded_cost_cr'],
            "mode": row_dict['mode_raw'] or "N/A",
            "status": row_dict['current_status'],
            "source_document": doc_raw,
            "source_document_label": SOURCE_DOC_LABELS.get(doc_raw, doc_raw),
            "state_raw": state_str,
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
    print("STAGE 7 FULL TABLE EXPORT SUMMARY (scripts/export_full_table.py -> DB v3)")
    print("=" * 80)
    print(f"Total Canonical Projects Exported : {len(projects_list)}")
    print("-" * 80)
    print("BREAKDOWN BY GEOCODE STATUS:")
    print("-" * 80)
    for status, count in sorted(status_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {status:45s}: {count:3d} projects")
    print("-" * 80)
    print("BREAKDOWN BY SOURCE DOCUMENT:")
    print("-" * 80)
    for doc, count in sorted(doc_counts.items(), key=lambda x: x[1], reverse=True):
        doc_lbl = SOURCE_DOC_LABELS.get(doc, doc)
        print(f"  - {doc_lbl:30s} ({doc}): {count:3d} projects")
    print("-" * 80)
    print(f"Output File: {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes)")
    print("=" * 80)


if __name__ == '__main__':
    export_full_table()
