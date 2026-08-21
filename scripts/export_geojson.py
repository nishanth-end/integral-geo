"""
Export script for NHAI Karnataka road projects.
Reads data from SQLite database (nhai_karnataka.db) and exports:
1. frontend/data/projects.geojson — placed projects with coordinates (LineString / Point features)
2. frontend/data/unplaced_projects.json — unplaced projects with plain-language reasons
"""

import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "nhai_karnataka.db"
FRONTEND_DATA_DIR = BASE_DIR / "frontend" / "data"
FRONTEND_DATA_DIR.mkdir(parents=True, exist_ok=True)

GEOJSON_PATH = FRONTEND_DATA_DIR / "projects.geojson"
UNPLACED_PATH = FRONTEND_DATA_DIR / "unplaced_projects.json"


def export_data(db_path: Path = None):
    if db_path is None:
        db_path = DB_PATH

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

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

    features = []
    unplaced = []

    count_linestrings = 0
    count_points = 0
    unplaced_reasons = {}

    for r in rows:
        row_dict = dict(r)
        lat_s = row_dict.get('lat_start')
        lng_s = row_dict.get('lng_start')
        lat_e = row_dict.get('lat_end')
        lng_e = row_dict.get('lng_end')
        conf = row_dict.get('geocode_confidence')
        method = row_dict.get('geocode_method')
        nh = row_dict.get('nh_number')
        ch_s = row_dict.get('chainage_start_km')

        # Placed in GeoJSON if coordinates are present and confidence is approximate
        if conf == 'approximate' and lat_s is not None and lng_s is not None:
            # Build feature properties
            props = {
                "id": row_dict['id'],
                "project_key": row_dict['project_key'],
                "nh_number": row_dict['nh_number'],
                "corridor_name": row_dict['corridor_name'],
                "chainage_start_km": row_dict['chainage_start_km'],
                "chainage_end_km": row_dict['chainage_end_km'],
                "package_label": row_dict['package_label'],
                "lanes_min": row_dict['lanes_min'],
                "lanes_max": row_dict['lanes_max'],
                "has_paved_shoulder": bool(row_dict['has_paved_shoulder']),
                "contractor_raw": row_dict['contractor_raw'],
                "state_raw": row_dict['state_raw'],
                "source_document": row_dict['source_document'],
                "geocode_method": row_dict['geocode_method'],
                "geocode_confidence": row_dict['geocode_confidence']
            }

            # Geometry: GeoJSON standard is [longitude, latitude]
            if lat_e is not None and lng_e is not None:
                geom = {
                    "type": "LineString",
                    "coordinates": [
                        [round(lng_s, 6), round(lat_s, 6)],
                        [round(lng_e, 6), round(lat_e, 6)]
                    ]
                }
                count_linestrings += 1
            else:
                geom = {
                    "type": "Point",
                    "coordinates": [round(lng_s, 6), round(lat_s, 6)]
                }
                count_points += 1

            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": props
            })
        else:
            # Determine clear plain-language failure reason
            if method == 'flagged_old_nh_numbering':
                reason = f"Uses pre-2010 NH numbering ({nh}) — needs manual review before mapping to current alignment"
            elif not nh:
                reason = "No NH number specified in source document"
            elif ch_s is None:
                reason = "No chainage data in source document"
            else:
                reason = f"Geocoding unavailable ({method or 'unresolved'}"

            unplaced_reasons[reason] = unplaced_reasons.get(reason, 0) + 1

            unplaced.append({
                "id": row_dict['id'],
                "project_key": row_dict['project_key'],
                "nh_number": row_dict['nh_number'],
                "corridor_name": row_dict['corridor_name'],
                "chainage_start_km": row_dict['chainage_start_km'],
                "chainage_end_km": row_dict['chainage_end_km'],
                "contractor_raw": row_dict['contractor_raw'],
                "state_raw": row_dict['state_raw'],
                "source_document": row_dict['source_document'],
                "reason": reason
            })

    geojson_doc = {
        "type": "FeatureCollection",
        "features": features
    }

    # Write GeoJSON
    with open(GEOJSON_PATH, "w", encoding="utf-8") as f:
        json.dump(geojson_doc, f, indent=2)

    # Write Unplaced Projects JSON
    with open(UNPLACED_PATH, "w", encoding="utf-8") as f:
        json.dump(unplaced, f, indent=2)

    conn.close()

    # Sanity checks and validation
    # Verify no null coordinates in geojson
    null_coords_found = False
    for feat in features:
        coords = feat['geometry']['coordinates']
        if feat['geometry']['type'] == 'Point':
            if None in coords:
                null_coords_found = True
        elif feat['geometry']['type'] == 'LineString':
            for pt in coords:
                if None in pt:
                    null_coords_found = True

    # Output verification summary
    print("=" * 80)
    print("STAGE 3 EXPORT SUMMARY (scripts/export_geojson.py)")
    print("=" * 80)
    print(f"Total Projects Processed:     {len(rows)}")
    print(f"Exported to GeoJSON:          {len(features)}")
    print(f"  - LineString Features:      {count_linestrings}")
    print(f"  - Point Features:           {count_points}")
    print(f"Exported to Unplaced JSON:    {len(unplaced)}")
    print("-" * 80)
    print("UNPLACED PROJECTS BREAKDOWN BY REASON:")
    print("-" * 80)
    for reason, count in unplaced_reasons.items():
        print(f"  - {reason}: {count} projects")
    print("-" * 80)
    print(f"GeoJSON File:                 {GEOJSON_PATH} ({GEOJSON_PATH.stat().st_size} bytes)")
    print(f"Unplaced Projects File:       {UNPLACED_PATH} ({UNPLACED_PATH.stat().st_size} bytes)")
    print(f"Null Coordinates in GeoJSON:  {'FAIL (Found nulls)' if null_coords_found else 'PASS (0 nulls)'}")
    print("=" * 80)


if __name__ == '__main__':
    export_data()
