# NHAI Karnataka Road Project Tracker

A public, open-source database and map tracking NHAI (National Highways
Authority of India) road projects in Karnataka — their contractors,
execution status, and (eventually) the officials responsible for them.

Built from publicly available NHAI data. The goal is simple: give citizens,
journalists, and researchers an accessible way to see who is building what,
where, under which contract, and how that compares to reality on the ground.

## Why

NHAI publishes project data, but it's scattered across inconsistent PDFs,
tender portals, and dashboards with no single accessible view. There is
currently no free, public tool that ties together **project + contractor +
geography + accountability** for Indian national highways. This project
exists to fill that gap, starting small and scoped rather than trying to
cover the whole country at once.

## Status

🚧 **Early development — Phase 1 (MVP).**

- [x] Phase 0 — scope locked, schema drafted, repo structured
- [x] Parsing script built and verified against real NHAI PDF data
- [x] Full Karnataka dataset parsed and cleaned (Under Implementation done;
      Awarded-not-Appointed and Completed documents pending)
- [x] SQLite database populated
- [x] Chainage-to-coordinate geocoding
- [x] First working map (Leaflet/Mapbox)
- [ ] Engineer/official data via RTI (not started — bottlenecked by RTI
      response turnaround, separate track from the rest of the build)

Not yet functional as a public tool. Currently a data pipeline in progress.

## Scope

- **Geography:** Karnataka only, for now. Deliberately not attempting
  national coverage in v1.
- **Agency:** NHAI (National Highways Authority of India) projects only.
- **Not covering (yet):** state highways, PWD roads, or private toll
  operators outside NHAI-awarded contracts.
- **Source documents:** NHAI's three standard project-status PDFs —
  *Under Implementation*, *Awarded but not Appointed*, and *Completed*.

Scope will expand only after the Karnataka MVP is stable and genuinely
useful — see [Contributing](#contributing).

## Data sources

- [NHAI](https://nhai.gov.in) — Under Implementation / Awarded / Completed
  project status PDFs
- [NHAI Data Lake](https://datalakeg.nhai.gov.in) — project GIS/status
  dashboard
- [Central Public Procurement Portal](https://eprocure.gov.in) — tender
  award documents
- RTI responses — for chief engineer / project-in-charge data (not yet
  collected)

Every dataset in this repo is traceable back to its source document —
see the `source_document` field in the schema.

## Project structure

```
nhai-project/
├── data/
│   ├── raw/            # unmodified source PDFs
│   └── processed/       # cleaned, structured JSON output
├── scripts/
│   ├── parse_projects.py    # core parsing logic
│   └── run_full_parse.py    # driver: PDF -> parsed JSON
├── schema/
│   └── schema.sql       # SQLite table definitions
├── frontend/             # map + UI (not started)
├── LICENSE
├── DATA_LICENSE
└── README.md
```

## How it works (current pipeline)

1. NHAI's project-status PDFs are parsed with `pdfplumber`, filtered to
   rows mentioning Karnataka.
2. `scripts/parse_projects.py` splits the messy free-text "Project Name"
   field into structured fields: corridor name, chainage range, package
   label, lane configuration — using regex logic tested against real
   NHAI data, not assumed formatting.
3. Output is written as structured JSON, then loaded into a SQLite
   database following the schema in `schema/schema.sql`.
4. (Planned) Chainage ranges are converted to geographic coordinates and
   rendered on an interactive map with project, contractor, and status
   details.

## Tech stack

- **Python** — parsing, data cleaning (`pdfplumber`, `re`)
- **SQLite** — storage, file-based, no server required for an MVP this size
- **Leaflet / Mapbox** — map rendering (planned)
- Parsing logic developed with local LLM assistance (Ollama, Qwen /
  Gemma) for iteration, verified independently against real source data
  before being trusted — every parsed field has been checked by hand
  against the original PDF text, not accepted on a model's word alone.

## License

- **Code** is licensed under the [GNU General Public License v3.0](LICENSE) —
  any redistributed modification must remain open source.
- **Compiled dataset** is licensed separately — see [DATA_LICENSE](DATA_LICENSE).
  Code and data licenses are kept distinct because GPL is written for
  software, not databases.
- Third-party source data (NHAI PDFs, government datasets) remains
  subject to its own original terms; this project does not claim
  ownership of the underlying government data, only of the structured,
  cleaned version compiled from it.

## Contributing

Not yet open for external contributions — the schema and pipeline are
still stabilizing. Once the Karnataka MVP is solid, this section will
cover how to flag stale data, add new states, or contribute to the
engineer/accountability data collection via RTI.

## Data accuracy & methodology

This project prioritizes traceability over completeness. Where source
data is ambiguous, garbled, or incomplete (e.g. malformed NH numbers,
missing chainage information), fields are left as `null` rather than
guessed — a missing value is preferable to a confidently wrong one on a
public accountability tool. Every parsed field can be traced back to its
source document and, eventually, the specific source row.
