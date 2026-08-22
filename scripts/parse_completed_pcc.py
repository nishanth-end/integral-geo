"""
Parser for NHAI Completed & PCC / PCOD Issue report:
MIS_august_O_AND_M_PCC.pdf

Nationwide report spanning all of India.
Filters to Karnataka rows (including multi-state packages),
extracts corridor name, chainage, length, mode, concessionaire,
and outputs data/processed/karnataka_completed_pcc_parsed.json.
"""

import json
import re
from pathlib import Path
from collections import Counter
import pdfplumber

BASE_DIR = Path(__file__).resolve().parent.parent
PDF_PATH = BASE_DIR / "data" / "raw" / "MIS_august_O_AND_M_PCC.pdf"
OUTPUT_DIR = BASE_DIR / "data" / "processed"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH = OUTPUT_DIR / "karnataka_completed_pcc_parsed.json"

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


def _clean_cell(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r'\s+', ' ', text.replace('\n', ' ')).strip()
    cleaned = cleaned.replace('BOT Annuit y', 'BOT Annuity')
    cleaned = cleaned.replace('BOT Annui ty', 'BOT Annuity')
    cleaned = cleaned.replace('BOT Ann uity', 'BOT Annuity')
    cleaned = cleaned.replace('Interm ediate', 'Intermediate')
    return cleaned


def _clean_nh_number(raw_nh: str) -> str:
    if not raw_nh:
        return None
    val = _clean_cell(raw_nh)
    if not val:
        return None
    if val.startswith("NH-") or val.startswith("NE-"):
        return val
    if val.startswith("NH"):
        return f"NH-{val[2:].strip()}"
    if val.startswith("NE"):
        return f"NE-{val[2:].strip()}"
    if re.match(r'^[0-9]+[A-Za-z]*$', val):
        return f"NH-{val}"
    return val


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


def extract_chainage(description: str):
    """
    Extract chainage start and end from description.
    Case-insensitive matching for km/ch prefixes.
    """
    NUM = r'\d+(?:\+\d+|\.\d+)?'
    range_pat = re.compile(
        rf'(?:km\.?|ch\.?)\s*({NUM})\s*(?:to|-)\s*(?:km\.?|ch\.?)?\s*({NUM})',
        re.IGNORECASE
    )
    m = range_pat.search(description)
    if m:
        start = process_km(m.group(1))
        end = process_km(m.group(2))
        if end < start:
            return start, None
        return start, end

    point_pat = re.compile(rf'(?:km\.?|ch\.?)\s*({NUM})', re.IGNORECASE)
    m2 = point_pat.search(description)
    if m2:
        return process_km(m2.group(1)), None

    return None, None


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
        r'stretch of\s+((?:(?!\bof\b|\bin the\b).)+?)'
        r'(?:\s+of\s+NH|\s+in the State|\s+for\b|\.|$)',
        description,
        re.IGNORECASE
    )
    if m2:
        return m2.group(1).strip()

    # From X to Y pattern
    m3 = re.search(
        r'(?:4L|6L|2L|Four laning|Six laning|Two laning)?\s*(?:of\s+)?([A-Za-z0-9\s\-–]+?\s*(?:to|-)\s*[A-Za-z0-9\s\-–]+?)(?:\s+from|\s+section|\s+Pkg|\s+in|\.|$)',
        description,
        re.IGNORECASE
    )
    if m3:
        corr_candidate = m3.group(1).strip()
        if len(corr_candidate) > 4 and not corr_candidate.lower().startswith('km'):
            return _trim_leading_preposition(corr_candidate)

    snippet = description.strip()[:60]
    return snippet + ('...' if len(description.strip()) > 60 else '')


def parse_pdf():
    print("=" * 80)
    print("PARSING MIS_august_O_AND_M_PCC.pdf (COMPLETED & PCC / PCOD ISSUE)")
    print("=" * 80)

    total_pages = 0
    total_raw_rows = 0
    candidate_data_rows = 0
    all_states_counter = Counter()
    karnataka_records = []
    karnataka_modes_counter = Counter()

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
                    state_raw = _clean_cell(row[10] if len(row) > 10 else '')
                    all_states_counter[state_raw] += 1

                    if "karnataka" in state_raw.lower():
                        sr_no = _clean_cell(row[0])
                        proj_name = _clean_cell(row[1])
                        raw_nh = _clean_cell(row[2])
                        raw_len = _clean_cell(row[3])
                        raw_loa = _clean_cell(row[4])
                        raw_start = _clean_cell(row[5])
                        raw_cost = _clean_cell(row[6])
                        raw_mode = _clean_cell(row[7])
                        raw_lanes = _clean_cell(row[8])
                        raw_concess = _clean_cell(row[9])

                        karnataka_modes_counter[raw_mode] += 1

                        ch_start, ch_end = extract_chainage(proj_name)
                        corridor = extract_corridor(proj_name)
                        nh = _clean_nh_number(raw_nh)
                        lanes_min, lanes_max = LANE_MAP.get(raw_lanes, (None, None))
                        has_ps = 'PS' in raw_lanes or 'paved shoulder' in proj_name.lower() or 'with ps' in proj_name.lower()
                        is_concession = raw_mode in ('HAM', 'BOT Toll', 'BOT Annuity', 'TOT', 'InvIT')

                        record = {
                            "sr_no": int(sr_no) if sr_no.isdigit() else sr_no,
                            "project_name_raw": proj_name,
                            "corridor_name": corridor,
                            "nh_number": nh,
                            "chainage_start_km": ch_start,
                            "chainage_end_km": ch_end,
                            "total_length_km": _parse_float(raw_len),
                            "loa_date_civil_work": raw_loa if raw_loa else None,
                            "appointed_date_contractor": raw_start if raw_start else None,
                            "total_awarded_cost_cr": _parse_float(raw_cost),
                            "mode": raw_mode if raw_mode else None,
                            "is_concession": is_concession,
                            "lanes_raw": raw_lanes if raw_lanes else None,
                            "lanes_min": lanes_min,
                            "lanes_max": lanes_max,
                            "has_paved_shoulder": has_ps,
                            "concessionaire": raw_concess if raw_concess else None,
                            "state_raw": state_raw,
                            "source_document": "MIS_august_O_AND_M_PCC.pdf",
                            "status": "Completed & PCC / PCOD Issued"
                        }
                        karnataka_records.append(record)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(karnataka_records, f, indent=2)

    # Sanity checks
    missing_nh = sum(1 for r in karnataka_records if not r["nh_number"])
    missing_ch = sum(1 for r in karnataka_records if r["chainage_start_km"] is None)
    missing_len = sum(1 for r in karnataka_records if r["total_length_km"] is None)
    missing_cost = sum(1 for r in karnataka_records if r["total_awarded_cost_cr"] is None)
    missing_concess = sum(1 for r in karnataka_records if not r["concessionaire"])

    print(f"Total Pages Scanned:              {total_pages}")
    print(f"Total Raw Table Rows:             {total_raw_rows}")
    print(f"Total Project Data Rows in PDF:   {candidate_data_rows}")
    print(f"Total Rows After Karnataka Filter:{len(karnataka_records)}")
    print("-" * 80)
    print("DISTINCT STATE STRINGS ENCOUNTERED IN NATIONWIDE PDF:")
    print("-" * 80)
    for st, cnt in sorted(all_states_counter.items(), key=lambda x: x[0]):
        print(f"  - {st:40s}: {cnt:4d} rows")
    print("-" * 80)
    print("DISTINCT MODE VALUES IN KARNATAKA ROWS:")
    print("-" * 80)
    for md, cnt in sorted(karnataka_modes_counter.items(), key=lambda x: x[1], reverse=True):
        is_conc = md in ('HAM', 'BOT Toll', 'BOT Annuity', 'TOT', 'InvIT')
        print(f"  - {md:20s}: {cnt:2d} rows {'(Concession-based)' if is_conc else '(EPC / Item Rate)'}")
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
    parse_pdf()
