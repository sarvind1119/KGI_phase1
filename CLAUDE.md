# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

KGI Choropleth Dashboard Generator — generates self-contained HTML dashboards showing up to 4 choropleth maps from GeoJSON boundary files and CSV data. Two interfaces: a CLI script and a Streamlit web app.

## Commands

```bash
# Install dependencies (in venv)
pip install -r requirements.txt   # pandas, streamlit

# Run the Streamlit web app
streamlit run app.py

# Run the CLI tool
python make_choropleth_chart.py <geojson_path> <csv_path> [--title TEXT] [--subtitle TEXT] [--theme light|dark] [--out PATH] [--name-prop NAME]
```

## Architecture

- **`make_choropleth_chart.py`** — the primary library and CLI. Exports: `auto_detect_name_prop`, `build_fuzzy_map`, `build_html`, `generate_insight`, `infer_sector`, `infer_unit`. The `build_html()` accepts a `sector_label` parameter and uses a global green→red log color scale.
- **`app.py`** — Streamlit app that imports from `make_choropleth_chart.py`. Two-tab workflow:
  - **"Shape Raw Data"** tab: loads raw KPI-entry data (Google Sheet auto-fetch with 5-min cache, local `KPI-entry.csv` fallback, or manual upload), filters by Sector → Ministry → KGI (up to 4), and pivots to wide-format CSV for download.
<<<<<<< HEAD
  - **"Generate Dashboard"** tab: accepts the wide-format CSV output above, matches regions to GeoJSON, allows per-KGI unit overrides, and renders the dashboard HTML.
  - Note: in the source, `tab2` is the Generate Dashboard tab and `tab1` is Shape Raw Data — the opposite of the display order in `st.tabs(["Shape Raw Data", "Generate Dashboard"])`.
  - Theme is hardcoded to `"light"` in the Streamlit app; the `--theme dark` option is CLI-only.
  - The "Enter your name" field (`app_user_name`) is used to prefix downloaded wide-format CSV filenames: `<user>_<ministry>.csv`.
  - `infer_dashboard_title()` derives the title from the selected ministry first, then falls back to the CSV filename stem.
=======
  - **"Generate Dashboard"** tab: accepts the wide-format CSV output above, matches regions to GeoJSON, and renders the dashboard HTML.
  - Note: in the source, `tab2` is the Generate Dashboard tab and `tab1` is Shape Raw Data — the opposite of the display order in `st.tabs(["Shape Raw Data", "Generate Dashboard"])`.
  - Theme is hardcoded to `"light"` in the Streamlit app; the `--theme dark` option is CLI-only.
>>>>>>> 52d1cf623f4eb18e0adee2ab5be2c908d0ea3f57

> `make_choropleth_dashboard.py` is referenced in older docs but **is not present in this repo**. Do not create it — `make_choropleth_chart.py` is the canonical implementation.

Key shared logic (in `make_choropleth_chart.py`):
- `build_fuzzy_map()` — 3-stage region name matcher (exact after alias resolution → substring → token overlap). The `KNOWN_ALIASES` dict inside it maps historical/alternate Indian state names.
- `auto_detect_name_prop()` — scans GeoJSON feature properties for common name keys (NAME_1, NAME, ST_NM, etc.)
- Numbers use Indian formatting: Cr (crore, 10^7), L (lakh, 10^5), K (10^3)

## Data Flow

**Wide-format CSV** (input to Generate Dashboard): column 1 = region names, columns 2–5 = up to 4 numeric indicators (headers become KGI labels).

<<<<<<< HEAD
**Raw KPI-entry CSV** (input to Shape Raw Data tab): must have columns `Cadre`, `Ministry/Department`, `KGI`, `Estimated figure`. Optional `Sector` column enables sector-level filtering (it is used for filtering only — not included in the pivoted output). Deduplication is on (`Cadre`, `KGI`), keeping first occurrence.
=======
**Raw KPI-entry CSV** (input to Shape Raw Data tab): must have columns `Cadre`, `Ministry/Department`, `KGI`, `Estimated figure`. Optional `Sector` column enables sector-level filtering. Deduplication is on (`Cadre`, `KGI`), keeping first occurrence.
>>>>>>> 52d1cf623f4eb18e0adee2ab5be2c908d0ea3f57

GeoJSON must be a FeatureCollection. The app defaults to `india.geojson` bundled in the repo. The Streamlit app fetches raw data from a hardcoded Google Sheet (ID: `1-QQvcuNZvXwlVwzkb7PnG7L7017gXsRNZrpZBJoFQLI`, GID: `1201847115`) with `KPI-entry.csv` local fallback.

## Key Considerations

- Output HTML embeds all data inline but loads D3.js 7.8.5 from CDN and Google Fonts externally — requires internet to render correctly.
- Zero and negative values are treated as "no data" (grey regions).
- Color scales are logarithmic (log10) to handle values spanning multiple orders of magnitude.
<<<<<<< HEAD
- `how-to-do-custom-modifications.md` and `make_choropleth_dashboard.py` are referenced in older docs but **are not present in this repo**. Do not create them.
=======
- `how-to-do-custom-modifications.md` is referenced in older docs but **is not present in this repo**.
>>>>>>> 52d1cf623f4eb18e0adee2ab5be2c908d0ea3f57
