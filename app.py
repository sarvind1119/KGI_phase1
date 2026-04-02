#!/usr/bin/env python3
"""
app.py — Streamlit KGI Choropleth Dashboard Application
========================================================
Two workflows:
  Tab 1: Upload a cleaned wide-format CSV → generate an interactive choropleth dashboard
  Tab 2: Upload a raw KPI-entry CSV → shape it (filter, pivot) → download or send to Tab 1

Run:  streamlit run app.py
"""

import io
import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from make_choropleth_chart import (
    auto_detect_name_prop,
    build_fuzzy_map,
    build_html,
    generate_insight,
    infer_sector,
    infer_unit,
)

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="KGI Choropleth Dashboard",
    page_icon="🗺️",
    layout="wide",
)

# ── Session state defaults ────────────────────────────────────────────────────

if "generated_html" not in st.session_state:
    st.session_state.generated_html = None
if "user_name" not in st.session_state:
    st.session_state.user_name = ""
if "app_user_name" not in st.session_state:
    st.session_state.app_user_name = st.session_state.user_name


# ── Helpers ───────────────────────────────────────────────────────────────────

APP_DIR = Path(__file__).resolve().parent
DEFAULT_GEOJSON = APP_DIR / "india.geojson"

# ── Google Sheet auto-load ────────────────────────────────────────────────────
GOOGLE_SHEET_ID = "1-QQvcuNZvXwlVwzkb7PnG7L7017gXsRNZrpZBJoFQLI"
GOOGLE_SHEET_GID = "1201847115"
GOOGLE_SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}"
    f"/export?format=csv&gid={GOOGLE_SHEET_GID}"
)
FALLBACK_CSV = APP_DIR / "KPI-entry.csv"


@st.cache_data(ttl=300, show_spinner="Fetching data from Google Sheet...")
def load_raw_data_from_sheet():
    """Fetch raw KPI data from the shared Google Sheet (CSV export)."""
    return pd.read_csv(GOOGLE_SHEET_CSV_URL)


def load_geojson(uploaded_file):
    """Load a GeoJSON from an uploaded file or fall back to the bundled default."""
    try:
        if uploaded_file is not None:
            raw = uploaded_file.read()
            uploaded_file.seek(0)
            geo = json.loads(raw)
            fname = uploaded_file.name
        else:
            if not DEFAULT_GEOJSON.exists():
                st.error("No GeoJSON uploaded and default india.geojson not found.")
                return None, None
            with open(DEFAULT_GEOJSON, encoding="utf-8") as f:
                geo = json.load(f)
            fname = DEFAULT_GEOJSON.name

        if geo.get("type") != "FeatureCollection" or not geo.get("features"):
            st.error("GeoJSON must be a FeatureCollection with at least one feature.")
            return None, None
        return geo, fname
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        st.error(f"Failed to parse GeoJSON: {exc}")
        return None, None


def run_dashboard_generation(
    wide_df,
    geojson_dict,
    geo_filename,
    csv_filename,
    title,
    theme,
    unit_overrides=None,
    national_kpi_values=None,
):
    """Build the HTML dashboard string from a wide-format DataFrame + GeoJSON."""
    name_prop = auto_detect_name_prop(geojson_dict)
    geo_names = [
        f["properties"].get(name_prop, "")
        for f in geojson_dict["features"]
        if f.get("properties")
    ]
    geo_names = [n for n in geo_names if n]

    state_col = wide_df.columns[0]
    ind_cols = list(wide_df.columns[1:5])  # up to 4 indicators

    # Coerce indicator columns to numeric
    for col in ind_cols:
        wide_df[col] = pd.to_numeric(wide_df[col], errors="coerce")

    # NaN warning
    total_cells = wide_df[ind_cols].size
    nan_cells = wide_df[ind_cols].isna().sum().sum()
    if total_cells > 0 and nan_cells / total_cells > 0.3:
        st.warning(
            f"**{nan_cells} of {total_cells}** indicator values could not be parsed as numbers. "
            "Those regions will appear blank/grey on the maps. "
            "Consider cleaning the data first."
        )

    csv_states = wide_df[state_col].dropna().astype(str).tolist()
    name_map = build_fuzzy_map(geo_names, csv_states)
    matched = sum(1 for v in name_map.values() if v)
    st.info(f"Matched **{matched}/{len(csv_states)}** CSV regions to GeoJSON features.")

    # Build KGI data objects
    kgis = []
    unit_overrides = unit_overrides or {}
    for col in ind_cols:
        data = {}
        for _, row in wide_df.iterrows():
            csv_name = str(row[state_col]) if pd.notna(row[state_col]) else None
            if not csv_name:
                continue
            geo_name = name_map.get(csv_name)
            if not geo_name:
                continue
            raw = row[col]
            if pd.isna(raw):
                continue
            v = float(raw)
            if v > 0:
                data[geo_name] = v
        selected_unit = (unit_overrides.get(col, "") or "").strip()
        unit = selected_unit or infer_unit(col)
        count = len(data)
        insight = generate_insight(data, unit)
        nkpi_raw = (national_kpi_values or {}).get(col)
        try:
            nkpi = float(nkpi_raw) if nkpi_raw is not None else None
        except (ValueError, TypeError):
            nkpi = None
        kgis.append({
            "code": col,
            "label": col,
            "unit": unit,
            "data": data,
            "count": count,
            "insight": insight,
            "national_kpi": nkpi,
        })

    if not kgis:
        st.error("No usable indicator data found after processing.")
        return None

    sector_label = infer_sector(csv_filename, ind_cols)

    html = build_html(
        geojson=geojson_dict,
        kgis=kgis,
        name_prop=name_prop,
        dashboard_title=title,
        dashboard_subtitle="",
        theme=theme,
        csv_filename=csv_filename,
        geo_filename=geo_filename,
        sector_label=sector_label,
    )
    return html


def df_to_csv_bytes(df):
    """Convert a DataFrame to CSV bytes for download."""
    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8")
    return buf.getvalue()


def sanitize_filename_part(value: str) -> str:
    """Sanitize one filename component for safe CSV naming."""
    cleaned = (value or "").strip()
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" ._")


def build_wide_csv_filename(user_name: str, ministry_name: str) -> str:
    """Build wide CSV filename as <user>_<ministry>.csv, or <ministry>.csv when user is blank."""
    safe_user = sanitize_filename_part(user_name)
    safe_ministry = sanitize_filename_part(ministry_name) or "wide_format"
    if safe_user:
        return f"{safe_user}_{safe_ministry}.csv"
    return f"{safe_ministry}.csv"


def infer_dashboard_title(csv_name: str, user_name: str = "", ministry_hint: str = "") -> str:
    """Infer dashboard title from ministry first, otherwise from the CSV file name."""
    if ministry_hint and ministry_hint.strip():
        return ministry_hint.strip()

    stem = Path(csv_name).stem
    if stem.lower().endswith("_wide"):
        stem = stem[:-5]

    safe_user = sanitize_filename_part(user_name)
    if safe_user and stem.lower().startswith(f"{safe_user.lower()}_"):
        stem = stem[len(safe_user) + 1:]

    cleaned = re.sub(r"[_\\-]+", " ", stem).strip()
    return cleaned or "Dashboard"


def make_streamlit_key(*parts: str) -> str:
    """Create stable Streamlit widget keys from arbitrary text."""
    raw = "_".join(parts)
    return re.sub(r"[^a-zA-Z0-9_]", "_", raw)


# ── App header ────────────────────────────────────────────────────────────────

st.title("KGI Choropleth Dashboard Generator")
st.caption("Generate interactive choropleth maps from your data, or shape raw KPI-entry files first.")
st.text_input("Enter your name", key="app_user_name", placeholder="Type your name here")
st.session_state.user_name = st.session_state.app_user_name

tab1, tab2 = st.tabs(["Shape Raw Data", "Generate Dashboard"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Generate Dashboard
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    wide_df = None
    csv_name = "uploaded.csv"

    csv_file = st.file_uploader(
        "Upload a wide-format CSV (the wide formatted data you downloaded from Tab 2)",
        type=["csv"],
        key="wf1_csv",
    )
    if csv_file is not None:
        try:
            wide_df = pd.read_csv(csv_file)
            csv_name = csv_file.name
        except Exception as exc:
            st.error(f"Failed to read CSV: {exc}")

    if wide_df is not None:
        # Detect and extract the national KPI marker row before any processing
        _nkpi_mask = wide_df.iloc[:, 0].astype(str).str.strip().str.lower() == "national kpi"
        national_kpi_values = {}
        if _nkpi_mask.any():
            _nkpi_row = wide_df[_nkpi_mask].iloc[0].to_dict()
            _nkpi_row.pop(wide_df.columns[0], None)
            national_kpi_values = _nkpi_row
            wide_df = wide_df[~_nkpi_mask].reset_index(drop=True)

        if wide_df.shape[1] < 2:
            st.error("CSV must have at least 2 columns (region names + at least 1 indicator).")
        else:
            with st.expander("Preview data", expanded=False):
                st.dataframe(wide_df.head(10), use_container_width=True)

            # GeoJSON
            geo_file = st.file_uploader(
                "Upload GeoJSON (optional — defaults to India)",
                type=["geojson", "json"],
                key="wf1_geojson",
            )

            indicator_cols = list(wide_df.columns[1:5])

            ministry_hint = ""

            title = infer_dashboard_title(
                csv_name=csv_name,
                user_name=st.session_state.user_name,
                ministry_hint=ministry_hint,
            )
            title_input_key = make_streamlit_key(
                "wf1_dashboard_title",
                csv_name,
                ministry_hint or "none",
            )
            title = st.text_input(
                "Dashboard Title",
                value=title,
                key=title_input_key,
                help="Auto-filled from Ministry/Department. You can edit this title.",
            )
            if not title.strip():
                title = infer_dashboard_title(
                    csv_name=csv_name,
                    user_name=st.session_state.user_name,
                    ministry_hint=ministry_hint,
                )

            st.markdown("**KGIs from uploaded CSV**")
            st.caption(", ".join(indicator_cols))

            st.markdown("**Enter unit for each KGI**")
            unit_overrides = {}
            kgi_cols = st.columns(min(4, len(indicator_cols)))
            for idx, kgi in enumerate(indicator_cols):
                default_unit = infer_unit(kgi)
                with kgi_cols[idx]:
                    unit_overrides[kgi] = st.text_input(
                        f"Unit: {kgi}",
                        value=default_unit,
                        key=make_streamlit_key("wf1_unit", str(idx), csv_name, kgi),
                        placeholder="e.g., km, units, ₹",
                    )

            # Theme is fixed now; users provide units per KGI.
            theme = "light"

            # Generate
            if st.button("Generate Dashboard", type="primary", key="wf1_generate"):
                geojson_dict, geo_fname = load_geojson(geo_file)
                if geojson_dict is not None:
                    with st.spinner("Building dashboard..."):
                        html = run_dashboard_generation(
                            wide_df.copy(), geojson_dict, geo_fname, csv_name,
                            title, theme, unit_overrides,
                            national_kpi_values=national_kpi_values,
                        )
                    if html:
                        st.session_state.generated_html = html

            # Output
            if st.session_state.generated_html:
                st.divider()
                st.subheader("Dashboard Preview")
                components.html(st.session_state.generated_html, height=750, scrolling=True)
                st.download_button(
                    "Download HTML",
                    data=st.session_state.generated_html,
                    file_name="dashboard.html",
                    mime="text/html",
                    key="wf1_download",
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Shape Raw Data
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    # ── Load data: Google Sheet → bundled CSV fallback → manual upload ────
    raw_df = None
    data_source = "none"

    try:
        raw_df = load_raw_data_from_sheet()
        data_source = "google_sheet"
        st.success("Loaded data from Google Sheet.")
    except Exception:
        if FALLBACK_CSV.exists():
            try:
                raw_df = pd.read_csv(FALLBACK_CSV)
                data_source = "fallback"
                st.warning("Could not reach Google Sheet. Using bundled local data.")
            except Exception:
                pass

    with st.expander("Or upload your own CSV"):
        raw_file = st.file_uploader(
            "Upload a raw KPI-entry CSV",
            type=["csv"],
            key="wf2_csv",
        )
        if raw_file is not None:
            try:
                raw_df = pd.read_csv(raw_file)
                data_source = "upload"
            except Exception as exc:
                st.error(f"Failed to read CSV: {exc}")

    if raw_df is not None:
        # Column validation
        required_cols = ["Cadre", "Ministry/Department", "KGI", "Estimated figure"]
        # Sector is used for filtering only, not included in final output
        needs_sector = "Sector" in raw_df.columns
        all_cols = required_cols + (["Sector"] if needs_sector else [])
        missing = [c for c in required_cols if c not in raw_df.columns]
        if missing:
            st.error(f"CSV is missing required columns: **{', '.join(missing)}**")
            st.caption(f"Found columns: {list(raw_df.columns)}")
            st.stop()

        # Keep required columns + Sector for filtering
        df = raw_df[all_cols].copy()

        st.info(f"Loaded **{len(df)}** rows across **{df['Ministry/Department'].nunique()}** ministries.")

        # Duplicate flagging
        df["Duplicate Flag"] = df.duplicated(subset=["Cadre", "KGI"], keep=False)
        n_dupes = df["Duplicate Flag"].sum()
        base_table = df.copy()  # long-format with flag

        # Deduplicate — keep first
        df_deduped = df.drop_duplicates(subset=["Cadre", "KGI"], keep="first").copy()
        if n_dupes > 0:
            st.caption(f"Found {n_dupes} duplicate (Cadre + KGI) rows; keeping first occurrence.")

        # Sector selection (if Sector column exists)
        if needs_sector:
            sectors = sorted(df_deduped["Sector"].dropna().unique().tolist())
            selected_sector = st.selectbox("Select Sector", sectors, key="wf2_sector")
            df_sector = df_deduped[df_deduped["Sector"] == selected_sector]
        else:
            df_sector = df_deduped

        # Ministry selection (filtered by sector)
        ministries = sorted(df_sector["Ministry/Department"].dropna().unique().tolist())
        selected_ministry = st.selectbox("Select Ministry/Department", ministries, key="wf2_ministry")

        # Filter to ministry
        df_ministry = df_sector[df_sector["Ministry/Department"] == selected_ministry]

        # KGI selection
        kgi_options = sorted(df_ministry["KGI"].dropna().unique().tolist())
        selected_kgis = st.multiselect(
            "Select up to 4 KGIs",
            options=kgi_options,
            max_selections=4,
            key="wf2_kgis",
        )

        if selected_kgis:
            # Filter and pivot
            df_filtered = df_ministry[df_ministry["KGI"].isin(selected_kgis)].copy()
            df_wide = df_filtered.pivot_table(
                index="Cadre",
                columns="KGI",
                values="Estimated figure",
                aggfunc="first",
            ).reset_index()

            # Reorder columns: Cadre first, then KGIs in selection order
            ordered_cols = ["Cadre"] + [k for k in selected_kgis if k in df_wide.columns]
            df_wide = df_wide[ordered_cols]

            # Extract National KPI per selected KGI and append as a marker row
            national_kpi_map = {}
            if "National KPI" in raw_df.columns:
                for kgi_name in selected_kgis:
                    subset = raw_df[raw_df["KGI"] == kgi_name]["National KPI"].dropna()
                    if not subset.empty:
                        national_kpi_map[kgi_name] = subset.iloc[0]
            if national_kpi_map:
                nkpi_row = {"Cadre": "National KPI"}
                nkpi_row.update({k: v for k, v in national_kpi_map.items() if k in df_wide.columns})
                df_wide = pd.concat([df_wide, pd.DataFrame([nkpi_row])], ignore_index=True)

            wide_filename = build_wide_csv_filename(
                st.session_state.user_name,
                selected_ministry,
            )

            # Preview
            st.subheader("Wide-format preview")
            st.dataframe(df_wide, use_container_width=True)

            with st.expander("Base table (long format with Duplicate Flag)", expanded=False):
                base_filtered = base_table[
                    (base_table["Ministry/Department"] == selected_ministry)
                    & (base_table["KGI"].isin(selected_kgis))
                ]
                # Drop Sector from the base table display/download (used only for filtering)
                base_display_cols = [c for c in base_filtered.columns if c != "Sector"]
                base_filtered = base_filtered[base_display_cols]
                st.dataframe(base_filtered, use_container_width=True)

            # Downloads
            col_a, col_b = st.columns(2)
            with col_a:
                st.download_button(
                    "Download Base Table (Long Format)",
                    data=df_to_csv_bytes(base_filtered),
                    file_name="base_table.csv",
                    mime="text/csv",
                    key="wf2_dl_long",
                )
            with col_b:
                st.download_button(
                    "Download Wide Format CSV",
                    data=df_to_csv_bytes(df_wide),
                    file_name=wide_filename,
                    mime="text/csv",
                    key="wf2_dl_wide",
                )

            # Guide to dashboard
            st.divider()
            st.info("Download the **Wide Format CSV** above, then switch to the **Generate Dashboard** tab and upload it there.")
        else:
            st.caption("Select at least 1 KGI to see the pivoted output.")
    else:
        if data_source == "none":
            st.error("No data available. Check your internet connection or upload a CSV manually using the expander above.")
