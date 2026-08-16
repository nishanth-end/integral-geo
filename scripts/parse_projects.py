"""
NHAI Karnataka road project parser.
Splits the messy 'Project Name' field from NHAI's Under Implementation PDF
into structured fields: corridor name, chainage range, package label, lanes.

Verified against 8 real Karnataka rows pulled from the source PDF.
"""
import re


def process_km(val_str):
    """Convert a chainage token like '123+456' or '123.456' into float km."""
    if '+' in val_str:
        whole, thousandths = val_str.split('+')
        return float(whole) + (float(thousandths) / 1000.0)
    return float(val_str)


def extract_chainage(description):
    """
    Priority:
    1. Explicit range: (Km|Ch)-prefixed number ... to ... (optionally
       Km|Ch-prefixed) number.
    2. Fallback: a single Km|Ch-prefixed point -> start only, end=None.
    3. No Km/Ch-prefixed number at all -> (None, None). This deliberately
       avoids misreading lengths/quantities like "20.00km" (unit AFTER
       the number) as a real location.
    """
    NUM = r'\d+(?:\+\d+|\.\d+)'  # atomic token: 123+456 or 123.456

    range_pat = re.compile(
        rf'(?:Km\.?|Ch\.?)\s*({NUM})\s*(?:to|-)\s*(?:Km\.?|Ch\.?)?\s*({NUM})'
    )
    m = range_pat.search(description)
    if m:
        start = process_km(m.group(1))
        end = process_km(m.group(2))
        if end < start:
            return start, None
        return start, end

    point_pat = re.compile(rf'(?:Km\.?|Ch\.?)\s*({NUM})')
    m2 = point_pat.search(description)
    if m2:
        return process_km(m2.group(1)), None

    return None, None


def _trim_leading_preposition(phrase):
    padded = ' ' + phrase
    cut = max(padded.rfind(' of '), padded.rfind(' on '))
    if cut != -1:
        phrase = padded[cut + 4:]
    return phrase.strip()


def extract_corridor(description):
    # Tier 1: up to 5 words immediately preceding "Section" (handles both
    # "... of X Section" and "... on X Section" phrasing).
    m = re.search(
        r'([A-Za-z0-9\-–]+(?:\s+[A-Za-z0-9\-–]+){0,4})\s+[Ss]ection\b',
        description
    )
    if m:
        return _trim_leading_preposition(m.group(1)) + ' Section'

    # Tier 2: "stretch of <name>" up to "of NH" / "in the State" / "for" / end
    m2 = re.search(
        r'stretch of\s+((?:(?!\bof\b|\bin the\b).)+?)'
        r'(?:\s+of\s+NH|\s+in the State|\s+for\b|\.|$)',
        description
    )
    if m2:
        return m2.group(1).strip()

    # Tier 3: fallback, first 60 chars — flagged with "..." so it's
    # visually obvious this wasn't a clean structured extraction.
    snippet = description.strip()[:60]
    return snippet + ('...' if len(description.strip()) > 60 else '')


LANE_MAP = {
    "2L": (2, 2), "4L": (4, 4), "6L": (6, 6), "8L": (8, 8),
    "4L/6L": (4, 6), "2L/4L": (2, 4), "4L/2L PS": (2, 4),
    "4L PS": (4, 4), "2L PS": (2, 2), "2L/4L PS": (2, 4),
    "Intermediate Lane (IL)": (1, 2),  # ~5.5m width, not a full 2-lane road
}


def _clean_cell(text):
    """PDF extraction wraps long cell text with embedded newlines, which
    sometimes split mid-word (e.g. 'Interm\\nediate\\nLane\\n(IL)').
    Collapse whitespace, then repair the one known mid-word split."""
    if not text:
        return ""
    cleaned = re.sub(r'\s+', ' ', text.replace('\n', ' ')).strip()
    cleaned = cleaned.replace('Interm ediate', 'Intermediate')
    return cleaned


def normalize_nh_number(nh_raw):
    """
    Real NH-number source values seen: 'NE-7', '48', '4', 'NH48', '648',
    '948A', and garbled multi-comma values like '4,44,444'.
    Returns a clean 'NH-XX' or 'NE-XX' string, or None if unparseable/garbled.
    """
    if not nh_raw or "," in nh_raw:
        return None  # comma = garbled source data, don't guess

    raw = nh_raw.strip().upper()

    ne_match = re.match(r'^NE-?(\d+)$', raw)
    if ne_match:
        return f"NE-{ne_match.group(1)}"

    # Strip any leading 'NH' or 'NH-' before re-adding a single clean prefix
    core = re.sub(r'^NH-?', '', raw)
    if core and re.match(r'^[0-9]+[A-Z]?$', core):
        return f"NH-{core}"

    return None


def parse_project_row(raw_list):
    """
    raw_list indices (actual NHAI Under Implementation PDF table structure,
    confirmed against the real extracted rows):
      0 = serial number, 1 = description, 2 = NH number, 3 = total length,
      4 = LOA date, 5 = appointed date, 6 = awarded cost (Rs Cr),
      7 = mode, 8 = lane code, 9 = contractor, 10 = state
    """
    description = _clean_cell(raw_list[1]) if len(raw_list) > 1 else ""
    nh_raw = _clean_cell(raw_list[2]) if len(raw_list) > 2 else ""
    lane_code = _clean_cell(raw_list[8]) if len(raw_list) > 8 else ""

    corridor_name = extract_corridor(description)
    nh_number = normalize_nh_number(nh_raw)
    chainage_start_km, chainage_end_km = extract_chainage(description)

    package_label = None
    pkg_match = re.search(r'\(Pkg-[IVX1235]+\)', description)
    if pkg_match:
        package_label = pkg_match.group(0).strip('()')

    if lane_code in LANE_MAP:
        lanes_min, lanes_max = LANE_MAP[lane_code]
    else:
        # Covers "Others" and any unrecognized code — None signals
        # "unknown," never silently defaults to 0.
        lanes_min, lanes_max = None, None

    has_paved_shoulder = "PS" in lane_code

    return {
        "corridor_name": corridor_name,
        "nh_number": nh_number,
        "chainage_start_km": chainage_start_km,
        "chainage_end_km": chainage_end_km,
        "package_label": package_label,
        "lanes_min": lanes_min,
        "lanes_max": lanes_max,
        "has_paved_shoulder": has_paved_shoulder,
    }


if __name__ == "__main__":
    raw_data = [
        ["232", "Construction of VOP near Sadahalli Gate at Km. 538.832 of Hyderabad – Bengaluru Section of NH-7 and Construction of Foot Over Bridge, bus shelters & bus bay at Km 490+850 and strengthening of (km 531 - km 534.7) and providing NJCB, Street Light in Median, Service Road of AP/Karnataka – Devanahalli section in the State of Karnataka on EPC mode.", "44", "EPC", "4L/6L"],
        ["614", "Installation and Commissioning of 5 Nos. of High Mast Lighting for the Project Four Laning of Hubli-Hospet Section of NH 67 (Old NH 67) from Km 128+850 to Km 272+571 in the State of Karnataka", "67", "Item Rate", "4L"],
        ["1096", "Maintenance of Jevargi – Shahapur section of NH-150A for re-surfacing of balance 20.00km bad reaches in the State of Karnataka", "150A", "Item Rate", "2L"],
        ["83", "Construction of LVUP at Javanagondanahalli at grade junction Ch.141+150 (KA-02-005) and Gorladaku at grade junction Ch.144+500 (KA-02-099) Under road safety for rectification of Blackspot in the stretch of Tumkur-Chitradurga section from Km 75+000 to 189+000 of NH-48 (Old NH-4) in the state of Karnataka", "48", "EPC", "6L"],
        ["451", "6L of MH/KN Border (Nimbal Village) to KN/TS Border (Singnodi Village) from Km. 26.000 to Km. 97.000 in Gulbarga of NH-150C (Pkg-II)", "150C", "HAM", "6L"],
        ["90", "Construction of Grade separators along with Service Roads at (1) Four lane VOPs at Veerasandra Junction at Chainages 19+395 & 19+465 (KA-(02)-115) (2) Four lane VOP at Old Chandapura at Chainage 25+180 (KA-(03)-35) (3) Four lane VOP at Guddahatti gate (Jain Temple) at Chainage 27+940 and (4) Four lane VOP at Guestline circle at Chainage 28+680 (KA-(03)-33) for remedial measures at accident prone locations in the stretch of Silk Board junction to KA/TN Border of NH-44 (Old NH-07) in the State of Karnataka under EPC mode", "4,44,444", "EPC", "6L"],
        ["582", "Construction of VOP at Nanthoor Junction (KA-02-135) at Km. 375+300 on NH-66 falls under completed four lane Highway stretch of New Mangalore Port Road Connectivity stretch from Surathkal Nantoor of NH-66 and Nantoor-Talapady of NH-73, developed under SPV to be executed on EPC mode", "66", "EPC", "4L PS"],
        ["576", "Construction of Long-Term Remedial measures of Landslide at 227+750 identified locations between Sakaleshpura to Marnahally on Hassan–Marnahally Section of NH-75 (Old NH-48) in the State of Karnataka on EPC mode", "75", "EPC", "Others"],
    ]

    for i, row in enumerate(raw_data, 1):
        r = parse_project_row(row)
        print(f"ROW {i}: {r}")
