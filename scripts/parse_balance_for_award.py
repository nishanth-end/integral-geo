"""
Parser for NHAI Balance For Award report:
Balance_for_award_MIS_august.pdf

Nationwide pre-award planning report (8 columns layout).
Columns: Sr. No. | Project Name | NH(New) | Total Length | Mode | Lanes | Dpr Name | State
Filters to Karnataka rows (including multi-state packages),
handles DPR consultants, TBD placeholders across fields, multiple NH numbers,
Total Length column-bleed corruption, multi-location chainage, and flexible multi-state splitting.

Outputs: data/processed/karnataka_balance_for_award_parsed.json
"""

import json
import re
from pathlib import Path
from collections import Counter
import pdfplumber

BASE_DIR = Path(__file__).resolve().parent.parent
PDF_PATH = BASE_DIR / "data" / "raw" / "Balance_for_award_MIS_august.pdf"
OUTPUT_DIR = BASE_DIR / "data" / "processed"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH = OUTPUT_DIR / "karnataka_balance_for_award_parsed.json"

LANE_MAP = {
    "2L": (2, 2),
    "2L PS": (2, 2),
    "2L/4L": (2, 4),
    "2L/4L PS": (2, 4),
    "4L/2L PS": (2, 4),
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


def _parse_nh_numbers(raw_nh: str, project_name: str):
    """
    Parses NH(New) column into a list of normalized NH numbers.
    Detects TBD placeholders and multiple highway designations in a single row.
    """
    if not raw_nh:
        return [], None, False, False

    val = _clean_cell(raw_nh)
    if not val:
        return [], None, False, False

    # Check for TBD or unassigned placeholders
    tbd_keywords = ['tbd', 'to be decided', 'to be declare', 'nh not decided yet', 'yet to be declare', 'green field', 'greenfield', 'multiple nhs', 'na']
    val_lower = val.lower().replace(' ', '').replace('-', '').replace('.', '')
    for kw in tbd_keywords:
        kw_clean = kw.replace(' ', '').replace('-', '').replace('.', '')
        if val_lower == kw_clean or val.upper() == 'TBD' or val.lower().startswith('to be') or val.lower().startswith('yet to'):
            return [], 'tbd_not_designated', True, False

    # Normalize format and split multiple NH entries
    v_norm = re.sub(r'(NH|NE|SH)[ -]+', r'\1-', val, flags=re.IGNORECASE)
    v_norm = re.sub(r'(\d+)\s+([A-Za-z])\b', r'\1\2', v_norm)
    tokens = re.split(r'[,&;/]|\band\b', v_norm)
    
    results = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        t_clean = re.sub(r'\(.*?\)', '', t).strip()
        if re.match(r'^(?:NH|NE|SH)-?[0-9]+[A-Za-z]*$', t_clean, re.IGNORECASE):
            prefix = t_clean[:2].upper()
            num = re.sub(r'^(?:NH|NE|SH)[ -]?', '', t_clean, flags=re.IGNORECASE)
            norm = f"{prefix}-{num.upper()}"
        elif re.match(r'^[0-9]+[A-Za-z]*$', t_clean):
            norm = f"NH-{t_clean.upper()}"
        else:
            norm = t_clean

        if norm and norm not in results:
            results.append(norm)

    has_multi = len(results) > 1
    return results, None, False, has_multi


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
        r'([A-Za-z0-9\-–]+(?:\s+[A-Za-z0-9\-–]+){0,5})\s+[Ss]ection\b',
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
        r'([A-Za-z0-9\-–]+(?:\s+[A-Za-z0-9\-–]+){0,3})\s+(?:Bypass|Ring Road|Ring road|Elevated Corridor|Tunnel)',
        description,
        re.IGNORECASE
    )
    if m3:
        return m3.group(0).strip()

    m4 = re.search(
        r'(?:Four Laning|Six Laning|Two Laning|4L|6L|2L)\s+of\s+([A-Za-z0-9\s\-–]+?\s*(?:to|-)\s*[A-Za-z0-9\s\-–]+?)(?:\s+from|\s+section|\s+in|\.|$)',
        description,
        re.IGNORECASE
    )
    if m4:
        return m4.group(1).strip()

    snippet = description.strip()[:60]
    return snippet + ('...' if len(description.strip()) > 60 else '')


def parse_balance_pdf():
    print("=" * 80)
    print("PARSING Balance_for_award_MIS_august.pdf (BALANCE FOR AWARD)")
    print("=" * 80)

    total_pages = 0
    total_raw_rows = 0
    candidate_data_rows = 0
    all_states_counter = Counter()
    all_pdf_modes_counter = Counter()
    karnataka_records = []
    karnataka_modes_counter = Counter()

    # Document-wide quality counters
    tbd_rows_count = 0
    multi_nh_rows_count = 0
    column_bleed_rows_count = 0
    multi_location_rows_count = 0
    anomalous_state_rows = []

    all_raw_data_rows = []

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
                    all_raw_data_rows.append((p_idx + 1, row))

    candidate_data_rows = len(all_raw_data_rows)

    for idx, (p_num, row) in enumerate(all_raw_data_rows):
        sr_no = _clean_cell(row[0])
        proj_name = _clean_cell(row[1])
        raw_nh = _clean_cell(row[2])
        raw_len = _clean_cell(row[3])
        raw_mode = _clean_cell(row[4])
        raw_lanes = _clean_cell(row[5])
        raw_dpr = _clean_cell(row[6])
        state_raw = _clean_cell(row[7] if len(row) > 7 else '')

        all_states_counter[state_raw] += 1
        all_pdf_modes_counter[raw_mode] += 1

        # Check for anomalous state strings across whole PDF
        state_parts = [s.strip() for s in state_raw.split(',') if s.strip()]
        for sp in state_parts:
            if sp not in INDIAN_STATES_UTS:
                anomalous_state_rows.append((p_num, sr_no, state_raw, sp))

        if "karnataka" in state_raw.lower():
            tbd_fields = []

            # 1. NH parsing & TBD
            nh_list, nh_note, nh_is_tbd, has_multi_nh = _parse_nh_numbers(raw_nh, proj_name)
            if nh_is_tbd:
                tbd_fields.append("nh_number")
            if has_multi_nh:
                multi_nh_rows_count += 1

            # 2. Mode & TBD
            mode_val = raw_mode
            if raw_mode.upper() == 'TBD' or raw_mode.lower() in ('not applicable', 'to be decided'):
                tbd_fields.append("mode")
                mode_val = None
            karnataka_modes_counter[raw_mode if raw_mode else "Empty/None"] += 1

            # 3. Lanes & TBD
            lanes_val = raw_lanes
            lanes_min, lanes_max = (None, None)
            if raw_lanes.upper() == 'TBD' or raw_lanes.lower() in ('not applicable', 'others'):
                tbd_fields.append("lanes")
                lanes_val = None
            elif raw_lanes in LANE_MAP:
                lanes_min, lanes_max = LANE_MAP[raw_lanes]

            if tbd_fields:
                tbd_rows_count += 1

            # 4. Total Length column-bleed check
            has_bleed = False
            bleed_note = None
            nh_digits = re.sub(r'[^0-9]', '', raw_nh)
            prev_nh_digits = re.sub(r'[^0-9]', '', _clean_cell(all_raw_data_rows[idx-1][1][2])) if idx > 0 else ''
            next_nh_digits = re.sub(r'[^0-9]', '', _clean_cell(all_raw_data_rows[idx+1][1][2])) if idx < len(all_raw_data_rows)-1 else ''

            if nh_digits and len(nh_digits) >= 2 and raw_len.endswith(nh_digits) and len(raw_len) > len(nh_digits) + 2:
                has_bleed = True
                bleed_note = f"Trailing digits in length ({raw_len}) match current row NH ({raw_nh})"
            elif prev_nh_digits and len(prev_nh_digits) >= 2 and raw_len.endswith(prev_nh_digits) and len(raw_len) > len(prev_nh_digits) + 2:
                has_bleed = True
                bleed_note = f"Trailing digits in length ({raw_len}) match previous row NH ({prev_nh_digits})"
            elif next_nh_digits and len(next_nh_digits) >= 2 and raw_len.endswith(next_nh_digits) and len(raw_len) > len(next_nh_digits) + 2:
                has_bleed = True
                bleed_note = f"Trailing digits in length ({raw_len}) match next row NH ({next_nh_digits})"

            if has_bleed:
                column_bleed_rows_count += 1

            # 5. Chainage and multi-location extraction
            ch_start, ch_end, has_multi_loc, loc_cnt = extract_chainage_and_multi_locations(proj_name)
            if has_multi_loc:
                multi_location_rows_count += 1

            corridor = extract_corridor(proj_name)
            has_ps = 'PS' in (raw_lanes or '') or 'paved shoulder' in proj_name.lower() or 'with ps' in proj_name.lower()
            is_concession = mode_val in ('HAM', 'BOT Toll', 'BOT Annuity', 'TOT', 'InvIT') if mode_val else False

            record = {
                "sr_no": int(sr_no) if sr_no.isdigit() else sr_no,
                "project_name_raw": proj_name,
                "corridor_name": corridor,
                "nh_numbers": nh_list,
                "has_multiple_nhs": has_multi_nh,
                "nh_note": nh_note,
                "total_length_km": _parse_float(raw_len),
                "has_length_column_bleed_warning": has_bleed,
                "length_bleed_note": bleed_note,
                "mode": mode_val,
                "is_concession": is_concession,
                "lanes_raw": lanes_val,
                "lanes_min": lanes_min,
                "lanes_max": lanes_max,
                "has_paved_shoulder": has_ps,
                "dpr_name": raw_dpr if raw_dpr else None,
                "state_raw": state_raw,
                "chainage_start_km": ch_start,
                "chainage_end_km": ch_end,
                "has_multiple_locations": has_multi_loc,
                "location_mentions_count": loc_cnt,
                "has_tbd_field": len(tbd_fields) > 0,
                "tbd_fields": tbd_fields,
                "source_document": "Balance_for_award_MIS_august.pdf",
                "status": "Balance For Award"
            }
            karnataka_records.append(record)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(karnataka_records, f, indent=2)

    # Sanity checks
    missing_nh = sum(1 for r in karnataka_records if not r["nh_numbers"])
    missing_ch = sum(1 for r in karnataka_records if r["chainage_start_km"] is None)
    missing_len = sum(1 for r in karnataka_records if r["total_length_km"] is None)
    missing_dpr = sum(1 for r in karnataka_records if not r["dpr_name"])
    missing_mode = sum(1 for r in karnataka_records if not r["mode"])

    print(f"Total Pages Scanned:              {total_pages}")
    print(f"Total Raw Table Rows:             {total_raw_rows}")
    print(f"Total Project Data Rows in PDF:   {candidate_data_rows}")
    print(f"Total Rows After Karnataka Filter:{len(karnataka_records)}")
    print("-" * 80)
    print("DISTINCT STATE STRINGS ENCOUNTERED IN NATIONWIDE PDF:")
    print("-" * 80)
    for st, cnt in sorted(all_states_counter.items(), key=lambda x: x[0]):
        print(f"  - {st:55s}: {cnt:3d} rows")
    print("-" * 80)
    print("ANOMALOUS / MISALIGNED STATE VALUES ACROSS NATIONWIDE PDF:")
    print("-" * 80)
    if anomalous_state_rows:
        for p_num, sr, st_raw, part in anomalous_state_rows:
            print(f"  - Page {p_num:3d}, Sr. No. {sr:>4s}: {repr(st_raw)} (Unrecognized token: {repr(part)})")
    else:
        print("  - None found")
    print("-" * 80)
    print("DISTINCT MODE VALUES (PDF-WIDE & KARNATAKA):")
    print("-" * 80)
    print("PDF-Wide Modes:")
    for md, cnt in sorted(all_pdf_modes_counter.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {md:20s}: {cnt:3d} rows")
    print("\nKarnataka Modes (Raw values in document):")
    for md, cnt in sorted(karnataka_modes_counter.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {md:20s}: {cnt:2d} rows")
    print("-" * 80)
    print("DOCUMENT-SPECIFIC QUALITY AUDIT COUNTERS (KARNATAKA ROWS):")
    print("-" * 80)
    print(f"  - Rows with any TBD field:                      {tbd_rows_count}/{len(karnataka_records)} rows")
    print(f"  - Rows with Multiple NH Numbers:                {multi_nh_rows_count}/{len(karnataka_records)} rows")
    print(f"  - Rows with Length Column-Bleed Warning:        {column_bleed_rows_count}/{len(karnataka_records)} rows")
    print(f"  - Multi-Location Chainage Flagged:              {multi_location_rows_count}/{len(karnataka_records)} rows")
    print("-" * 80)
    print("FIELD COMPLETENESS SANITY CHECKS (KARNATAKA ROWS):")
    print("-" * 80)
    print(f"  - Missing/TBD NH Numbers:       {missing_nh}/{len(karnataka_records)}")
    print(f"  - Missing Chainage (Start):     {missing_ch}/{len(karnataka_records)}")
    print(f"  - Missing Total Length:         {missing_len}/{len(karnataka_records)}")
    print(f"  - Missing Mode (TBD/None):      {missing_mode}/{len(karnataka_records)}")
    print(f"  - Missing DPR Consultant:       {missing_dpr}/{len(karnataka_records)}")
    print("-" * 80)
    print(f"Output File Written:              {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes)")
    print("=" * 80)


if __name__ == '__main__':
    parse_balance_pdf()
