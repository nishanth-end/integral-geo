"""
Geocoding module for NHAI Karnataka road projects.
Extracts and stitches complete OpenStreetMap highway geometry centerlines,
persists them into the SQLite highway_geometry table, and interpolates project
coordinates along the route based on chainage values.

NOTE ON ACCURACY:
Coordinates produced by this module are marked as geocode_confidence='approximate'.
Reason:
1. OpenStreetMap (OSM) highway way geometries begin and end at administrative boundaries
   or road junction nodes, rather than the official NHAI zero-kilometer monument.
2. Official NHAI chainage marks are physical surveyed milestones along historical alignments
   that account for road centerlines, curves, and bypasses, which may have slight offsets
   from crowd-sourced OpenStreetMap linestring representations.
3. Where chainage values are absent (NULL), coordinates are strictly left NULL and marked
   as geocode_confidence='failed' to prevent inaccurate positional guesses.
4. Old pre-renumbering highway numbers (e.g. NH-4, NH-4A, NH-206) are flagged with
   geocode_method='flagged_old_nh_numbering' for manual review rather than guessing.
"""

import json
import math
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from collections import defaultdict
import heapq
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "nhai_karnataka.db"
CACHE_DIR = BASE_DIR / "data" / "osm_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Karnataka Bounding Box
KARNATAKA_BBOX = "11.5,74.0,18.5,78.6"

# Old pre-2010 NH numbers that must be flagged for manual review rather than silently resolved
OLD_NH_NUMBERS = {"NH-4", "NH-4A", "NH-206", "NH-13", "NH-17", "NH-218"}

# Overpass API endpoints with failover
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points in meters."""
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points in kilometers."""
    return haversine_m(lat1, lon1, lat2, lon2) / 1000.0


def init_geometry_table(conn: sqlite3.Connection):
    """Ensure highway_geometry table exists in the database."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS highway_geometry (
            nh_number TEXT PRIMARY KEY,
            geometry_json TEXT NOT NULL,
            point_count INTEGER NOT NULL,
            total_length_km REAL NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def fetch_osm_elements(nh_number: str) -> list:
    """Fetch and cache raw OSM ways and relations for a given NH in Karnataka."""
    clean = nh_number.upper().strip()
    cache_file = CACHE_DIR / f"{clean}.json"

    if cache_file.exists() and cache_file.stat().st_size > 100:
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    prefix = "NE" if clean.startswith("NE") else "NH"
    num_part = clean.replace("NH-", "").replace("NE-", "").replace("NH", "").replace("NE", "").strip()

    pattern = f"^({prefix}[ -]?)?{num_part}$"
    if re.search(r"[A-Za-z]", num_part):
        base_num = re.sub(r"[A-Za-z]", "", num_part)
        pattern = f"^({prefix}[ -]?)?({num_part}|{num_part}A|{base_num})$"

    query = f"""
    [out:json][timeout:45];
    (
      relation["route"="road"]["ref"~"{pattern}",i]({KARNATAKA_BBOX});
      way["highway"]["ref"~"{pattern}",i]({KARNATAKA_BBOX});
    );
    out geom;
    """

    data_enc = urllib.parse.urlencode({"data": query}).encode("utf-8")

    for ep in OVERPASS_ENDPOINTS:
        try:
            req = urllib.request.Request(
                ep,
                data=data_enc,
                headers={"User-Agent": "NHAI-Karnataka-Accountability-Tool/1.0"}
            )
            with urllib.request.urlopen(req, timeout=50) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                elements = res.get("elements", [])
                if elements:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(elements, f)
                    time.sleep(2.0)
                    return elements
        except Exception:
            time.sleep(2.0)
            continue

    return []


def stitch_way_segments(segments: list, start_target: tuple = None, end_target: tuple = None, snap_tolerance_m: float = 120.0) -> tuple:
    """
    Stitch list of way segments into ONE continuous ordered path by matching shared/nearest endpoints.
    Returns (stitched_path, point_count, total_length_km).
    """
    if not segments:
        return [], 0, 0.0

    node_coords = {}
    node_id_map = {}

    def get_node_id(pt):
        # Snap within ~20m (4 decimal places is ~11m)
        key = (round(pt[0], 4), round(pt[1], 4))
        if key not in node_id_map:
            nid = len(node_coords)
            node_id_map[key] = nid
            node_coords[nid] = pt
        return node_id_map[key]

    adj = defaultdict(list)
    for seg in segments:
        if len(seg) < 2:
            continue
        u = get_node_id(seg[0])
        v = get_node_id(seg[-1])
        d = sum(haversine_m(seg[i][0], seg[i][1], seg[i+1][0], seg[i+1][1]) for i in range(len(seg)-1))
        adj[u].append((v, d, seg, False))
        adj[v].append((u, d, seg[::-1], True))

    nodes = list(node_coords.items())
    grid = defaultdict(list)
    for nid, pt in nodes:
        cell = (int(pt[0] * 100), int(pt[1] * 100))
        grid[cell].append((nid, pt))

    for cell, nlist in grid.items():
        candidates = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidates.extend(grid.get((cell[0]+dx, cell[1]+dy), []))
        for nid1, pt1 in nlist:
            for nid2, pt2 in candidates:
                if nid1 < nid2:
                    d = haversine_m(pt1[0], pt1[1], pt2[0], pt2[1])
                    if 0 < d <= snap_tolerance_m:
                        adj[nid1].append((nid2, d * 1.2, [pt1, pt2], False))
                        adj[nid2].append((nid1, d * 1.2, [pt2, pt1], True))

    all_pts = list(node_coords.values())
    lats = [p[0] for p in all_pts]
    lons = [p[1] for p in all_pts]
    lat_span = max(lats) - min(lats)
    lon_span = max(lons) - min(lons)

    if start_target is None:
        if lat_span >= lon_span:
            start_target = min(all_pts, key=lambda p: p[0])
            end_target = max(all_pts, key=lambda p: p[0])
        else:
            start_target = min(all_pts, key=lambda p: p[1])
            end_target = max(all_pts, key=lambda p: p[1])

    start_node = min(node_coords.keys(), key=lambda n: haversine_km(node_coords[n][0], node_coords[n][1], start_target[0], start_target[1]))
    end_node = min(node_coords.keys(), key=lambda n: haversine_km(node_coords[n][0], node_coords[n][1], end_target[0], end_target[1]))

    dist = {start_node: 0.0}
    prev = {}
    pq = [(0.0, start_node)]

    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, float('inf')):
            continue
        if u == end_node:
            break
        for v, weight, seg_pts, is_rev in adj[u]:
            new_d = d + weight
            if new_d < dist.get(v, float('inf')):
                dist[v] = new_d
                prev[v] = (u, seg_pts)
                heapq.heappush(pq, (new_d, v))

    if end_node in dist and dist[end_node] > 1000.0:
        curr = end_node
        stitched_path = []
        while curr != start_node:
            p_u, seg_pts = prev[curr]
            stitched_path = seg_pts[1:] + stitched_path
            curr = p_u
        stitched_path = [node_coords[start_node]] + stitched_path
    else:
        oriented = []
        for s in segments:
            if (lat_span >= lon_span and s[-1][0] < s[0][0]) or (lat_span < lon_span and s[-1][1] < s[0][1]):
                s = s[::-1]
            oriented.append(s)
        oriented.sort(key=lambda s: (s[0][0], s[0][1]) if lat_span >= lon_span else (s[0][1], s[0][0]))
        stitched_path = list(oriented[0])
        for s in oriented[1:]:
            if (lat_span >= lon_span and s[-1][0] > stitched_path[-1][0]) or (lat_span < lon_span and s[-1][1] > stitched_path[-1][1]):
                stitched_path.extend(s)

    # Subsample consecutive near-identical points (min 40m spacing)
    clean_path = [stitched_path[0]]
    for p in stitched_path[1:]:
        if haversine_m(clean_path[-1][0], clean_path[-1][1], p[0], p[1]) >= 40.0:
            clean_path.append(p)

    total_km = sum(haversine_km(clean_path[i-1][0], clean_path[i-1][1], clean_path[i][0], clean_path[i][1]) for i in range(1, len(clean_path)))
    return clean_path, len(clean_path), round(total_km, 2)


def get_highway_centerline(nh_number: str) -> tuple:
    """Fetch and stitch highway centerline for a given NH."""
    clean_nh = nh_number.upper().strip()
    elements = fetch_osm_elements(clean_nh)
    if clean_nh == "NH-48" and (CACHE_DIR / "NH48_KA_relation.json").exists():
        with open(CACHE_DIR / "NH48_KA_relation.json", "r", encoding="utf-8") as f:
            rel = json.load(f)
            elements = rel.get("members", []) + (elements or [])

    if not elements:
        return [], 0, 0.0

    segments = []
    for el in elements:
        geom = el.get("geometry", [])
        if len(geom) >= 2:
            pts = [(round(p["lat"], 6), round(p["lon"], 6)) for p in geom]
            pts_ka = [p for p in pts if 11.4 <= p[0] <= 18.6 and 73.8 <= p[1] <= 78.8]
            if len(pts_ka) >= 2:
                segments.append(pts_ka)

    start_target, end_target = None, None
    if clean_nh == "NH-48":
        start_target = (13.0339, 77.5309) # Bangalore / Nelamangala (Km 0)
        end_target = (16.6893, 74.2740)   # Maharashtra Border (Km 592)

    return stitch_way_segments(segments, start_target, end_target)


def ensure_highway_geometries(conn: sqlite3.Connection, nh_list: list) -> dict:
    """Ensure all required NH geometries are computed and persisted in highway_geometry table."""
    init_geometry_table(conn)
    cursor = conn.cursor()

    highway_paths = {}

    for nh in nh_list:
        if not nh or nh in OLD_NH_NUMBERS:
            continue

        cursor.execute("SELECT geometry_json, point_count, total_length_km FROM highway_geometry WHERE nh_number = ?", (nh,))
        row = cursor.fetchone()

        if row and row[1] > 0 and row[2] > 0:
            path = json.loads(row[0])
            highway_paths[nh] = (path, row[2])
        else:
            path, count, total_len = get_highway_centerline(nh)
            if path and count > 0 and total_len > 0:
                geom_json = json.dumps(path)
                cursor.execute("""
                    INSERT OR REPLACE INTO highway_geometry (nh_number, geometry_json, point_count, total_length_km, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (nh, geom_json, count, total_len))
                conn.commit()
                highway_paths[nh] = (path, total_len)

    return highway_paths


def interpolate_along_path(path: list, total_path_len: float, target_km: float) -> tuple:
    """Interpolate (lat, lon) at target_km distance along ordered path."""
    if not path:
        return None, None
    if len(path) == 1 or total_path_len == 0:
        return path[0]

    target_km = max(0.0, min(target_km, total_path_len))

    accum = 0.0
    for i in range(1, len(path)):
        seg_d = haversine_km(path[i - 1][0], path[i - 1][1], path[i][0], path[i][1])
        if accum + seg_d >= target_km:
            if seg_d == 0:
                return path[i]
            frac = (target_km - accum) / seg_d
            lat = path[i - 1][0] + frac * (path[i][0] - path[i - 1][0])
            lon = path[i - 1][1] + frac * (path[i][1] - path[i - 1][1])
            return round(lat, 6), round(lon, 6)
        accum += seg_d

    return path[-1]


def geocode_database(db_path: Path = None):
    """Main geocoding runner."""
    if db_path is None:
        db_path = DB_PATH

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Query all projects
    cursor.execute("""
        SELECT id, project_key, nh_number, corridor_name, package_label,
               chainage_start_km, chainage_end_km
        FROM projects
        ORDER BY id
    """)
    projects = cursor.fetchall()

    distinct_active_nhs = sorted({p[2] for p in projects if p[2] and p[2] not in OLD_NH_NUMBERS and p[5] is not None})

    # Cache geometries in DB
    highway_geometries = ensure_highway_geometries(conn, distinct_active_nhs)

    # Sanity check: Print geometry stats from the cache table
    cursor.execute("SELECT nh_number, point_count, total_length_km FROM highway_geometry ORDER BY nh_number")
    cached_geoms = cursor.fetchall()

    print("=" * 80)
    print("STEP 1 SANITY CHECK: HIGHWAY GEOMETRY CACHE TABLE (nhai_karnataka.db)")
    print("=" * 80)
    for nh, count, length in cached_geoms:
        print(f"  {nh:10s}: {count:5d} points | Cumulative Path Length: {length:7.2f} km")
    print("=" * 80)

    # Process all projects
    approx_count = 0
    failed_count = 0
    flagged_old_count = 0
    sample_geocoded = []

    for p in projects:
        proj_id, p_key, nh, corridor, pkg, ch_s, ch_e = p

        # Handle old pre-renumbering NH numbers
        if nh in OLD_NH_NUMBERS:
            cursor.execute("""
                UPDATE projects
                SET lat_start = NULL, lng_start = NULL,
                    lat_end = NULL, lng_end = NULL,
                    geocode_method = 'flagged_old_nh_numbering',
                    geocode_confidence = 'failed'
                WHERE id = ?
            """, (proj_id,))
            flagged_old_count += 1
            continue

        # Condition: No chainage or missing NH geometry
        if ch_s is None or not nh or nh not in highway_geometries:
            cursor.execute("""
                UPDATE projects
                SET lat_start = NULL, lng_start = NULL,
                    lat_end = NULL, lng_end = NULL,
                    geocode_method = NULL,
                    geocode_confidence = 'failed'
                WHERE id = ?
            """, (proj_id,))
            failed_count += 1
            continue

        path, total_len = highway_geometries[nh]

        # Interpolate coordinates along the continuous path
        lat_s, lng_s = interpolate_along_path(path, total_len, ch_s)

        lat_e, lng_e = None, None
        straight_dist = None
        if ch_e is not None:
            lat_e, lng_e = interpolate_along_path(path, total_len, ch_e)
            straight_dist = haversine_km(lat_s, lng_s, lat_e, lng_e)

        cursor.execute("""
            UPDATE projects
            SET lat_start = ?, lng_start = ?,
                lat_end = ?, lng_end = ?,
                geocode_method = 'osm_chainage_linear_interpolation',
                geocode_confidence = 'approximate'
            WHERE id = ?
        """, (lat_s, lng_s, lat_e, lng_e, proj_id))
        approx_count += 1

        if len(sample_geocoded) < 5:
            sample_geocoded.append((proj_id, p_key, nh, corridor, ch_s, ch_e, lat_s, lng_s, lat_e, lng_e, straight_dist))

    conn.commit()

    # Step 5: Verification Summary
    print()
    print("=" * 80)
    print("STAGE 2 VERIFICATION SUMMARY (ROUND 2 FIX)")
    print("=" * 80)
    print(f"Total Projects:                     {len(projects)}")
    print(f"Projects Geocoded (approximate):     {approx_count}")
    print(f"Projects Failed (no chainage/null):  {failed_count}")
    print(f"Projects Flagged (old NH numbers):   {flagged_old_count}")
    print("-" * 80)
    print("5-ROW SAMPLE FOR MANUAL SPOT-CHECK VERIFICATION:")
    print("-" * 80)
    for s in sample_geocoded:
        pid, pkey, nh, corr, chs, che, lats, lngs, late, lnge, sdist = s
        print(f"Project ID: {pid:2d} | Key: {pkey}")
        print(f"  NH Number:       {nh}")
        print(f"  Corridor:        {corr}")
        print(f"  Chainage Range:  Km {chs} to Km {che if che is not None else 'N/A'}")
        print(f"  Start Position:  ({lats:.6f}, {lngs:.6f})")
        if late is not None:
            print(f"  End Position:    ({late:.6f}, {lnge:.6f})")
            print(f"  Straight Dist:   {sdist:.2f} km (chainage delta = {che - chs:.2f} km)")
        else:
            print(f"  End Position:    N/A (single point chainage)")
        print(f"  Geocode Method:  osm_chainage_linear_interpolation")
        print(f"  Confidence:      approximate")
        print()
    print("=" * 80)

    conn.close()


if __name__ == '__main__':
    geocode_database()
