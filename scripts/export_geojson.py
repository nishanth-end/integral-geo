"""
Export script for NHAI Karnataka road projects (Schema v3).
Reads from data/nhai_karnataka_v3.db by querying canonical_projects
joined with their current project_snapshots, highway_geometry, and entity/junction tables.

Exports:
1. frontend/data/projects.geojson — placed projects with full curved LineString and Point features
2. frontend/data/unplaced_projects.json — unplaced projects with plain-language reasons
"""

import json
import math
import sqlite3
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "nhai_karnataka_v3.db"
FRONTEND_DATA_DIR = BASE_DIR / "frontend" / "data"
FRONTEND_DATA_DIR.mkdir(parents=True, exist_ok=True)

GEOJSON_PATH = FRONTEND_DATA_DIR / "projects.geojson"
UNPLACED_PATH = FRONTEND_DATA_DIR / "unplaced_projects.json"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points in kilometers."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def extract_subpath(path: list, total_len: float, ch_start: float, ch_end: float) -> list:
    """
    Extracts the full ordered curved sequence of intermediate coordinates along
    the highway centerline between ch_start and ch_end.
    Returns GeoJSON format [[lon, lat], ...].
    """
    if not path:
        return []
    if len(path) == 1 or total_len == 0 or ch_start is None or ch_end is None:
        return [[round(path[0][1], 6), round(path[0][0], 6)]]

    c_min = max(0.0, min(min(ch_start, ch_end), total_len))
    c_max = max(0.0, min(max(ch_start, ch_end), total_len))

    cum_dist = [0.0]
    for i in range(1, len(path)):
        d = haversine_km(path[i-1][0], path[i-1][1], path[i][0], path[i][1])
        cum_dist.append(cum_dist[-1] + d)

    subpath = []

    # 1. Exact start interpolation
    start_pt = None
    start_seg_idx = 0
    for i in range(1, len(path)):
        if cum_dist[i-1] <= c_min <= cum_dist[i]:
            seg_d = cum_dist[i] - cum_dist[i-1]
            if seg_d == 0:
                start_pt = (path[i][0], path[i][1])
            else:
                frac = (c_min - cum_dist[i-1]) / seg_d
                lat = path[i-1][0] + frac * (path[i][0] - path[i-1][0])
                lon = path[i-1][1] + frac * (path[i][1] - path[i-1][1])
                start_pt = (lat, lon)
            start_seg_idx = i
            break
    if start_pt is None:
        start_pt = (path[-1][0], path[-1][1])
        start_seg_idx = len(path)

    subpath.append(start_pt)

    # 2. Add all intermediate vertices along the curved road
    for k in range(start_seg_idx, len(path)):
        if cum_dist[k] < c_max:
            if cum_dist[k] > c_min:
                subpath.append((path[k][0], path[k][1]))
        else:
            break

    # 3. Exact end interpolation
    end_pt = None
    for i in range(max(1, start_seg_idx), len(path)):
        if cum_dist[i-1] <= c_max <= cum_dist[i]:
            seg_d = cum_dist[i] - cum_dist[i-1]
            if seg_d == 0:
                end_pt = (path[i][0], path[i][1])
            else:
                frac = (c_max - cum_dist[i-1]) / seg_d
                lat = path[i-1][0] + frac * (path[i][0] - path[i-1][0])
                lon = path[i-1][1] + frac * (path[i][1] - path[i-1][1])
                end_pt = (lat, lon)
            break
    if end_pt is None:
        end_pt = (path[-1][0], path[-1][1])

    if haversine_km(subpath[-1][0], subpath[-1][1], end_pt[0], end_pt[1]) > 0.001:
        subpath.append(end_pt)

    # 4. Preserve direction corresponding to chainage order
    if ch_start > ch_end:
        subpath = subpath[::-1]

    # Convert to GeoJSON standard [longitude, latitude]
    return [[round(p[1], 6), round(p[0], 6)] for p in subpath]


def export_data(db_path: Path = None):
    if db_path is None:
        db_path = DB_PATH

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Load highway geometries
    cursor.execute("SELECT nh_number, geometry_json, total_length_km FROM highway_geometry")
    highway_geometries = {
        r['nh_number']: (json.loads(r['geometry_json']), r['total_length_km'])
        for r in cursor.fetchall()
    }

    # Fetch entities mapped by snapshot_key
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

    # Fetch NH numbers mapped by snapshot_key
    cursor.execute("""
        SELECT snapshot_key, nh_number, is_state_highway
        FROM project_nh_numbers
        ORDER BY snapshot_key, nh_number
    """)
    nhs_by_snap = defaultdict(list)
    for r in cursor.fetchall():
        nhs_by_snap[r['snapshot_key']].append(r['nh_number'])

    # Fetch States mapped by snapshot_key
    cursor.execute("""
        SELECT snapshot_key, state_name
        FROM project_states
        ORDER BY snapshot_key, state_name
    """)
    states_by_snap = defaultdict(list)
    for r in cursor.fetchall():
        states_by_snap[r['snapshot_key']].append(r['state_name'])

    # Fetch Canonical Projects joined with their current active snapshot
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

    features = []
    unplaced = []

    count_linestrings = 0
    count_points = 0
    linestring_point_counts = []
    unplaced_reasons = defaultdict(int)

    for r in rows:
        row_dict = dict(r)
        snap_key = row_dict['snapshot_key']
        can_id = row_dict['canonical_project_id']
        lat_s = row_dict.get('lat_start')
        lng_s = row_dict.get('lng_start')
        lat_e = row_dict.get('lat_end')
        lng_e = row_dict.get('lng_end')
        conf = row_dict.get('geocode_confidence')
        method = row_dict.get('geocode_method')
        ch_s = row_dict.get('chainage_start_km')
        ch_e = row_dict.get('chainage_end_km')

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
            primary_role = roles_present[0].capitalize()
        else:
            ent_names = []
            ent_display = "Not listed"
            primary_role = "None"

        # Check if successfully geocoded
        if conf == 'approximate' and lat_s is not None and lng_s is not None:
            props = {
                "id": row_dict['sr_no_source'],
                "canonical_project_id": can_id,
                "project_key": snap_key,
                "nh_number": primary_nh,
                "nh_numbers": nh_list,
                "corridor_name": row_dict['corridor_name'] or row_dict['current_corridor_name'],
                "chainage_start_km": ch_s,
                "chainage_end_km": ch_e,
                "total_length_km": row_dict['total_length_km'],
                "package_label": row_dict['lanes_raw'] or "N/A",
                "lanes_min": row_dict['lanes_min'],
                "lanes_max": row_dict['lanes_max'],
                "has_paved_shoulder": bool(row_dict['has_paved_shoulder']),
                "contractor_raw": ent_display,
                "contractors": ent_names,
                "entity_role": primary_role,
                "cost_cr": row_dict['total_awarded_cost_cr'],
                "mode": row_dict['mode_raw'],
                "status": row_dict['current_status'],
                "source_document": row_dict['source_document'],
                "state_raw": state_str,
                "geocode_method": method,
                "geocode_confidence": conf
            }

            # If both start and end coordinates exist, slice the full curved centerline geometry
            if lat_e is not None and lng_e is not None and primary_nh in highway_geometries and ch_e is not None:
                path, t_len = highway_geometries[primary_nh]
                curved_coords = extract_subpath(path, t_len, ch_s, ch_e)
                if len(curved_coords) >= 2:
                    geom = {
                        "type": "LineString",
                        "coordinates": curved_coords
                    }
                    count_linestrings += 1
                    linestring_point_counts.append(len(curved_coords))
                else:
                    geom = {
                        "type": "Point",
                        "coordinates": [round(lng_s, 6), round(lat_s, 6)]
                    }
                    count_points += 1
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
            # Determine plain-language reason for unplaced register
            if method == 'flagged_old_nh_numbering':
                reason = f"Uses pre-2010 NH numbering ({primary_nh}) — requires manual alignment mapping"
            elif method == 'missing_nh_identifier' or not nh_list or primary_nh == 'NO-NH':
                reason = "No designated NH number in source document (TBD / unassigned)"
            elif method == 'skipped_no_chainage' or ch_s is None:
                reason = "No chainage milestone data in source document"
            elif method == 'nh_not_found_in_osm':
                reason = f"Highway alignment not yet mapped in OpenStreetMap ({primary_nh})"
            else:
                reason = f"Geocoding unavailable ({method or 'unresolved'})"

            unplaced_reasons[reason] += 1

            unplaced.append({
                "id": row_dict['sr_no_source'],
                "canonical_project_id": can_id,
                "project_key": snap_key,
                "nh_number": primary_nh,
                "corridor_name": row_dict['corridor_name'] or row_dict['current_corridor_name'],
                "chainage_start_km": ch_s,
                "chainage_end_km": ch_e,
                "contractor_raw": ent_display,
                "entity_role": primary_role,
                "status": row_dict['current_status'],
                "source_document": row_dict['source_document'],
                "state_raw": state_str,
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

    # Sanity checks
    null_coords_found = False
    for feat in features:
        coords = feat['geometry']['coordinates']
        if feat['geometry']['type'] == 'Point':
            if None in coords or len(coords) < 2:
                null_coords_found = True
        elif feat['geometry']['type'] == 'LineString':
            for pt in coords:
                if None in pt or len(pt) < 2:
                    null_coords_found = True

    avg_pts = sum(linestring_point_counts) / len(linestring_point_counts) if linestring_point_counts else 0
    min_pts = min(linestring_point_counts) if linestring_point_counts else 0
    max_pts = max(linestring_point_counts) if linestring_point_counts else 0

    print("=" * 80)
    print("STAGE 8 CURVED LINESTRING EXPORT SUMMARY (scripts/export_geojson.py)")
    print("=" * 80)
    print(f"Total Canonical Projects Processed : {len(rows)}")
    print(f"Exported to GeoJSON (approximate)  : {len(features)}")
    print(f"  - LineString Features (curved)   : {count_linestrings}")
    print(f"  - Point Features (single-point)  : {count_points}")
    print(f"Exported to Unplaced JSON (failed) : {len(unplaced)}")
    print("-" * 80)
    print("LINESTRING VERTEX COUNT STATISTICS:")
    print("-" * 80)
    print(f"  - Average vertices per LineString: {avg_pts:.1f} points (was 2.0)")
    print(f"  - Min vertices on a LineString   : {min_pts} points")
    print(f"  - Max vertices on a LineString   : {max_pts} points")
    print(f"  - Total vertices across all lines: {sum(linestring_point_counts)} points")
    print("-" * 80)
    print(f"GeoJSON File Size                  : {GEOJSON_PATH.stat().st_size / 1024:.1f} KB (was ~154 KB)")
    print(f"Null Coordinates Check             : {'FAIL (Found nulls)' if null_coords_found else 'PASS (0 null coordinates)'}")
    print("=" * 80)


if __name__ == '__main__':
    export_data()
