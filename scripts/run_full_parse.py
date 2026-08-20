import pdfplumber
import json
from parse_projects import parse_project_row, _clean_cell

PDF_PATH = "../data/raw/Under_implementation_MIS_august.pdf"
STATE_FILTER = "Karnataka"

rows = []
with pdfplumber.open(PDF_PATH) as pdf:
    for page in pdf.pages:
        for table in page.extract_tables():
            for row in table:
                if row and any(cell and STATE_FILTER in cell for cell in row):
                    rows.append(row)

print(f"Found {len(rows)} rows mentioning {STATE_FILTER}")

results = []
for row in rows:
    parsed = parse_project_row(row)
    parsed["contractor"] = _clean_cell(row[9]) if len(row) > 9 else None
    parsed["state_raw"] = _clean_cell(row[10]) if len(row) > 10 else None
    results.append(parsed)

with open("../data/karnataka_parsed.json", "w") as f:
    json.dump(results, f, indent=2)

# Sanity checks — read these yourself, don't just trust "it ran"
no_nh = sum(1 for r in results if not r["nh_number"])
no_chainage = sum(1 for r in results if r["chainage_start_km"] is None)
print(f"Missing NH number: {no_nh}/{len(results)}")
print(f"Missing chainage: {no_chainage}/{len(results)}")