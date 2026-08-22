"""
Parser for NHAI Awarded But Not Started report:
MIS_Aug_Awarded_not_appointed.pdf

Nationwide report spanning all of India (10 columns table structure).
Filters to Karnataka rows, handles scientific-notation corruption in NH numbers,
TBD placeholders, duplicate NH digits, paved shoulder (PS) lane suffixes,
multi-location chainage references, and non-appointed date design.

Outputs: data/processed/karnataka_awarded_not_appointed_parsed.json
"""

import json
import re
from pathlib import Path
from collections import Counter
import pdfplumber

BASE_DIR = Path(__file__).resolve().parent.parent
PDF_PATH = BASE_DIR / "data" / "raw" / "MIS_Aug_Awarded_not_appointed.pdf"
OUTPUT_DIR = BASE_DIR / "data" / "processed"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH = OUTPUT_DIR / "karnataka_awarded_not_appointed_parsed.json"

LANE_MAP = {
    "2L": (2, 2),
    "2L PS": (2, 2),
    "2L/4L": (2, 4),
    "2L/4L PS": (2, 4),
    "4L": (4, 4),
    "4L PS": (4, 4),
    "4L/6L": (4, 6),
    "6L": (6, 6),
    "6L/8L": (6, 8),
    "8L": (8, 8),
    "Intermediate Lane (IL)": (1, 2),
}

INDIAN_STATES_UTS = {
    'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh',
    'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jharkhand', 'Karnataka',
    'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram',
    'Nagaland', 'Odisha', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu',
    'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
    'Andaman and Nicobar Islands', 'Chandigarh', 'Dadra and Nagar Haveli and Daman and Diu',
    'Delhi', 'Jammu and Kashmir', 'Jammu And Kashmir', 'Ladakh', 'Lakshadweep', 'Puducherry'
}


def _clean_cell(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r'\s+', ' ', text.replace('\n', ' ')).strip()
    cleaned = cleaned.replace('BOT Annuit y', 'BOT Annuity')
    cleaned = cleaned.replace('BOT Annui ty', 'BOT Annuity')
    cleaned = cleaned.replace('BOT Ann uity', 'BOT Annuity')
    cleaned = cleaned.replace('Interm ediate', 'Intermediate')
    return cleaned


def _parse_nh_number(raw_nh: str, project_name: str):
    """
    Parse NH(New) field and handle data-quality issues:
    1. Scientific notation (e.g. 4E+05) -> return (None, 'corrupted_scientific_notation')
    2. Literal 'TBD' -> return (None, 'tbd_not_designated')
    3. Normalizes valid NH numbers
    4. Cross-checks against project_name text for discrepancy/concatenated digits
    """
    if not raw_nh:
        return None, None, False

    val = _clean_cell(raw_nh)
    if not val:
        return None, None, False

    # Issue 1: Scientific notation (e.g. 4E+05, 5E+05)
    if re.search(r'^\d+[Ee]\+\d+$', val):
        return None, f"corrupted_scientific_notation: {val}", True

    # Issue 3: Literal TBD
    if val.upper() == 'TBD':
        return None, "tbd_not_designated", False

    # Extract any NH mentioned in project_name
    text_nhs = re.findall(r'(?:NH|National Highway)[ -]?([0-9]+[A-Za-z]*)', project_name, re.IGNORECASE)

    # Normalize valid NH format
    cleaned_nh = val
    if not (cleaned_nh.startswith("NH-") or cleaned_nh.startswith("NE-")):
        if cleaned_nh.startswith("NH"):
            cleaned_nh = f"NH-{cleaned_nh[2:].strip()}"
        elif cleaned_nh.startswith("NE"):
            cleaned_nh = f"NE-{cleaned_nh[2:].strip()}"
        elif re.match(r'^[0-9]+[A-Za-z]*$', cleaned_nh):
            cleaned_nh = f"NH-{cleaned_nh}"

    # Issue 2: Cross-check discrepancy (e.g. duplicated digits or mismatch with text)
    num_part = re.sub(r'^(?:NH|NE)[ -]?', '', cleaned_nh)
    has_discrepancy = False
    discrepancy_note = None

    if text_nhs and num_part not in text_nhs:
        has_discrepancy = True
        discrepancy_note = f"nh_column ({cleaned_nh}) differs from text mentions ({', '.join(text_nhs)})"

    return cleaned_nh, discrepancy_note, has_discrepancy


def _parse_float(val: str):
    if not val:
        return None
    cleaned = _clean_cell(val).replace(',', '')
    try:
        return float(cleaned)
    except ValueError:
        return None


def process_km(val_str: str) -> float:
    if '+' in val_str:
        whole, thousandths = val_str.split('+')
        return float(whole) + (float(thousandths) / 1000.0)
    return float(val_str)


def extract_chainage_and_multi_locations(description: str):
    """
    Extract chainage start/end and count distinct location mentions.
    Flags has_multiple_locations: True if multiple distinct segments/ranges are bundled.
    """
    NUM = r'\d+(?:\+\d+|\.\d+)?'
    range_pat = re.compile(
        rf'(?:km\.?|ch\.?)\s*({NUM})\s*(?:to|–|-)\s*(?:km\.?|ch\.?)?\s*({NUM})',
        re.IGNORECASE
    )
    ranges = range_pat.findall(description)

    point_pat = re.compile(rf'(?:km\.?|ch\.?)\s*({NUM})', re.IGNORECASE)
    points = point_pat.findall(description)

    start, end = None, None
    if ranges:
        start = process_km(ranges[0][0])
        end = process_km(ranges[0][1])
        if end < start:
            start, end = start, None
    elif points:
        start = process_km(points[0])

    has_multi = (len(ranges) > 1) or (len(ranges) == 1 and len(points) > 2) or (len(ranges) == 0 and len(points) > 1)
    location_count = max(len(ranges), len(points))

    return start, end, has_multi, location_count


def _trim_leading_preposition(phrase: str) -> str:
    padded = ' ' + phrase
    cut = max(padded.rfind(' of '), padded.rfind(' on '))
    if cut != -1:
        phrase = padded[cut + 4:]
    return phrase.strip()


def extract_corridor(description: str) -> str:
    m = re.search(
        r'([A-Za-z0-9\-–]+(?:\s+[A-Za-z0-9\-–]+){0,4})\s+[Ss]ection\b',
        description
    )
    if m:
        return _trim_leading_preposition(m.group(1)) + ' Section'

    m2 = re.search(
        r'bypassed\s+(?:sections?|stretch)\s+of\s+((?:(?!\bon\b|\bof\b|\bin\b).)+?)(?:\s+on\s+NH|\s+in|\.|$)',
        description,
        re.IGNORECASE
    )
    if m2:
        return f"Bypassed stretch of {m2.group(1).strip()}"

    m3 = re.search(
        r'stretch of\s+((?:(?!\bof\b|\bin the\b).)+?)'
        r'(?:\s+of\s+NH|\s+in the State|\s+for\b|\.|$)',
        description,
        re.IGNORECASE
    )
    if m3:
        return m3.group(1).strip()

    snippet = description.strip()[:60]
    return snippet + ('...' if len(description.strip()) > 60 else '')


def parse_awarded_pdf():
    print("=" * 80)
    print("PARSING MIS_Aug_Awarded_not_appointed.pdf (AWARDED BUT NOT STARTED)")
    print("=" * 80)

    total_pages = 0
    total_raw_rows = 0
    candidate_data_rows = 0
    all_states_counter = Counter()
    all_pdf_modes_counter = Counter()
    karnataka_records = []
    karnataka_modes_counter = Counter()

    # Document-wide quality counters
    scientific_nh_count = 0
    tbd_nh_count = 0
    discrepancy_nh_count = 0
    anomalous_state_rows = []

    with pdfplumber.open(PDF_PATH) as pdf:
        total_pages = len(pdf.pages)
        for p_idx, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for t in tables:
                for row in t:
                    total_raw_rows += 1
                    if not row or not any(row):
                        continue
                    first_cell = (row[0] or '').strip()
                    if not first_cell.isdigit():
                        continue

                    candidate_data_rows += 1
                    sr_no = _clean_cell(row[0])
                    proj_name = _clean_cell(row[1])
                    raw_nh = _clean_cell(row[2])
                    raw_len = _clean_cell(row[3])
                    raw_loa = _clean_cell(row[4])
                    raw_cost = _clean_cell(row[5])
                    raw_mode = _clean_cell(row[6])
                    raw_lanes = _clean_cell(row[7])
                    raw_concess = _clean_cell(row[8])
                    state_raw = _clean_cell(row[9] if len(row) > 9 else '')

                    all_states_counter[state_raw] += 1
                    all_pdf_modes_counter[raw_mode] += 1

                    # Check for scientific notation and TBD across whole PDF
                    if re.search(r'^\d+[Ee]\+\d+$', raw_nh):
                        scientific_nh_count += 1
                    if raw_nh.upper() == 'TBD':
                        tbd_nh_count += 1

                    # Check for anomalous state strings across whole PDF
                    state_parts = [s.strip() for s in state_raw.split(',') if s.strip()]
                    for sp in state_parts:
                        if sp not in INDIAN_STATES_UTS:
                            anomalous_state_rows.append((p_idx + 1, sr_no, state_raw, sp))

                    # Parse Karnataka rows
                    if "karnataka" in state_raw.lower():
                        karnataka_modes_counter[raw_mode] += 1

                        nh, nh_note, nh_disc = _parse_nh_number(raw_nh, proj_name)
                        if nh_disc:
                            discrepancy_nh_count += 1

                        ch_start, ch_end, has_multi, loc_cnt = extract_chainage_and_multi_locations(proj_name)
                        corridor = extract_corridor(proj_name)

                        lanes_min, lanes_max = LANE_MAP.get(raw_lanes, (None, None))
                        has_ps = 'PS' in raw_lanes or 'paved shoulder' in proj_name.lower() or 'with ps' in proj_name.lower()
                        is_concession = raw_mode in ('HAM', 'BOT Toll', 'BOT Annuity', 'TOT', 'InvIT')

                        record = {
                            "sr_no": int(sr_no) if sr_no.isdigit() else sr_no,
                            "project_name_raw": proj_name,
                            "corridor_name": corridor,
                            "nh_number": nh,
                            "nh_note": nh_note,
                            "has_nh_discrepancy": nh_disc,
                            "chainage_start_km": ch_start,
                            "chainage_end_km": ch_end,
                            "has_multiple_locations": has_multi,
                            "location_mentions_count": loc_cnt,
                            "total_length_km": _parse_float(raw_len),
                            "loa_date_civil_work": raw_loa if raw_loa else None,
                            "appointed_date_contractor": None,  # None by design for Awarded Not Started
                            "total_awarded_cost_cr": _parse_float(raw_cost),
                            "mode": raw_mode if raw_mode else None,
                            "is_concession": is_concession,
                            "lanes_raw": raw_lanes if raw_lanes else None,
                            "lanes_min": lanes_min,
                            "lanes_max": lanes_max,
                            "has_paved_shoulder": has_ps,
                            "concessionaire": raw_concess if raw_concess else None,
                            "state_raw": state_raw,
                            "source_document": "MIS_Aug_Awarded_not_appointed.pdf",
                            "status": "Awarded But Not Started"
                        }
                        karnataka_records.append(record)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(karnataka_records, f, indent=2)

    # Sanity checks for Karnataka rows
    missing_nh = sum(1 for r in karnataka_records if not r["nh_number"])
    missing_ch = sum(1 for r in karnataka_records if r["chainage_start_km"] is None)
    missing_len = sum(1 for r in karnataka_records if r["total_length_km"] is None)
    missing_cost = sum(1 for r in karnataka_records if r["total_awarded_cost_cr"] is None)
    missing_concess = sum(1 for r in karnataka_records if not r["concessionaire"])
    ps_count = sum(1 for r in karnataka_records if r["has_paved_shoulder"])
    multi_loc_count = sum(1 for r in karnataka_records if r["has_multiple_locations"])

    print(f"Total Pages Scanned:              {total_pages}")
    print(f"Total Raw Table Rows:             {total_raw_rows}")
    print(f"Total Project Data Rows in PDF:   {candidate_data_rows}")
    print(f"Total Rows After Karnataka Filter:{len(karnataka_records)}")
    print("-" * 80)
    print("DISTINCT STATE STRINGS ENCOUNTERED IN NATIONWIDE PDF:")
    print("-" * 80)
    for st, cnt in sorted(all_states_counter.items(), key=lambda x: x[0]):
        print(f"  - {st:45s}: {cnt:3d} rows")
    print("-" * 80)
    print("ANOMALOUS STATE VALUES ACROSS NATIONWIDE PDF:")
    print("-" * 80)
    if anomalous_state_rows:
        for p_num, sr, st_raw, part in anomalous_state_rows:
            print(f"  - Page {p_num:2d}, Sr. No. {sr:4s}: {repr(st_raw)} (Unrecognized: {repr(part)})")
    else:
        print("  - None found (All State cells match standard Indian States/UTs)")
    print("-" * 80)
    print("DISTINCT MODE VALUES (PDF-WIDE & KARNATAKA):")
    print("-" * 80)
    print("PDF-Wide Modes:")
    for md, cnt in sorted(all_pdf_modes_counter.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {md:20s}: {cnt:3d} rows")
    print("\nKarnataka Modes:")
    for md, cnt in sorted(karnataka_modes_counter.items(), key=lambda x: x[1], reverse=True):
        is_conc = md in ('HAM', 'BOT Toll', 'BOT Annuity', 'TOT', 'InvIT')
        print(f"  - {md:20s}: {cnt:2d} rows {'(Concession-based)' if is_conc else '(EPC / Item Rate)'}")
    print("-" * 80)
    print("DOCUMENT-SPECIFIC QUALITY AUDIT COUNTERS:")
    print("-" * 80)
    print(f"  - Corrupted Scientific Notation NHs (PDF-wide): {scientific_nh_count} rows")
    print(f"  - 'TBD' Unassigned NH Numbers (PDF-wide):       {tbd_nh_count} rows")
    print(f"  - Paved Shoulder ('PS') Suffix (Karnataka):     {ps_count}/{len(karnataka_records)} rows")
    print(f"  - Multi-Location Chainage Flagged (Karnataka):  {multi_loc_count}/{len(karnataka_records)} rows")
    print(f"  - NH Number Discrepancies (Karnataka):          {discrepancy_nh_count}/{len(karnataka_records)} rows")
    print("-" * 80)
    print("FIELD COMPLETENESS SANITY CHECKS (KARNATAKA ROWS):")
    print("-" * 80)
    print(f"  - Missing NH Number:            {missing_nh}/{len(karnataka_records)}")
    print(f"  - Missing Chainage (Start):     {missing_ch}/{len(karnataka_records)}")
    print(f"  - Missing Total Length:         {missing_len}/{len(karnataka_records)}")
    print(f"  - Missing Awarded Cost:         {missing_cost}/{len(karnataka_records)}")
    print(f"  - Missing Concessionaire:       {missing_concess}/{len(karnataka_records)}")
    print("-" * 80)
    print(f"Output File Written:              {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes)")
    print("=" * 80)


if __name__ == '__main__':
    parse_awarded_pdf()
