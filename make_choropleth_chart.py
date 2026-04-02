#!/usr/bin/env python3
"""
make_choropleth_chart.py
────────────────────────
Generate a self-contained HTML choropleth dashboard from a GeoJSON map file
and a CSV data file.  Bigscreen-optimised layout with centered headings,
fixed panel heights, and auto-generated per-indicator insights.

Usage
-----
    python make_choropleth_chart.py <geojson_path> <csv_path> [options]

Positional arguments
--------------------
    geojson_path   Path to a GeoJSON FeatureCollection (one feature per region).
    csv_path       Path to a CSV where:
                     • Column 1  : region names (matched against GeoJSON)
                     • Columns 2–5 : up to 4 numeric indicators (headers used as titles)

Optional arguments
------------------
    --name-prop    GeoJSON property that holds the region name  [default: auto-detect]
    --title        Dashboard heading text                        [default: derived from CSV filename]
    --subtitle     Sub-heading text shown below the title
    --out          Output HTML file path                         [default: <csv_stem>_dashboard.html]
    --theme        Color theme: 'light' (default) or 'dark'

Examples
--------
    python make_choropleth_chart.py india.geojson urban_data.csv
    python make_choropleth_chart.py india.geojson kpi.csv --title "Agriculture KPIs" --out agri.html
    python make_choropleth_chart.py states.geojson data.csv --name-prop STATE_NAME --theme dark
"""

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

# ── third-party (only pandas + built-ins required) ────────────────────────────
try:
    import pandas as pd
except ImportError:
    sys.exit("ERROR: pandas is required.  Install with:  pip install pandas")

# ── colour palettes ────────────────────────────────────────────────────────────
# Each palette: (accent_hex, light_tint_hex)
PALETTES = [
    ("#2563eb", "#dbeafe"),   # blue
    ("#dc2626", "#fee2e2"),   # red
    ("#d97706", "#fde68a"),   # amber
    ("#059669", "#a7f3d0"),   # green
]

DARK_PALETTES = [
    ("#60a5fa", "#1e3a5f"),
    ("#f87171", "#4c1919"),
    ("#fbbf24", "#3d2a00"),
    ("#34d399", "#064e3b"),
]


# ── helpers ────────────────────────────────────────────────────────────────────

def auto_detect_name_prop(geojson: dict) -> str:
    """Return the most likely property key holding region names."""
    candidates = ["NAME_1", "NAME", "name", "NAME_0", "ST_NM",
                  "state_name", "STNAME", "ADM1_EN", "shapeName"]
    if not geojson.get("features"):
        return "name"
    props = geojson["features"][0].get("properties", {})
    for c in candidates:
        if c in props:
            return c
    # fall back to first string-valued property
    for k, v in props.items():
        if isinstance(v, str):
            return k
    return list(props.keys())[0] if props else "name"


def build_fuzzy_map(geo_names: list[str], csv_names: list[str]) -> dict:
    """
    Build a dict  csv_name -> geo_name  using case-insensitive substring matching.
    Also handles common patterns like 'AGMUT (Delhi)' -> 'Delhi'.
    """
    # Hard aliases: normalised CSV token -> normalised GeoJSON token
    KNOWN_ALIASES: dict[str, str] = {
        "pondicherry": "puducherry",
        "pondy":       "puducherry",
        "uttaranchal": "uttarakhand",
        "orissa":      "odisha",
        "bombay":      "maharashtra",
        "madras":      "tamil nadu",
        "bangalore":   "karnataka",
        "andaman nicobar": "andaman and nicobar",
        "daman diu":   "dadra and nagar haveli and daman and diu",
        "dadra nagar haveli": "dadra and nagar haveli and daman and diu",
        "dnhdd":       "dadra and nagar haveli and daman and diu",
        "j k":         "jammu and kashmir",
        "j&k":         "jammu and kashmir",
        "jk":          "jammu and kashmir",
    }

    def normalise(s: str) -> str:
        s = s.lower().strip()
        # strip AGMUT (…) / state (…) wrapper
        m = re.search(r'\(([^)]+)\)', s)
        if m:
            s = m.group(1).strip()
        s = re.sub(r'[^a-z0-9 ]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return KNOWN_ALIASES.get(s, s)

    geo_norm = {normalise(g): g for g in geo_names}

    mapping = {}
    for csv_name in csv_names:
        cn = normalise(csv_name)
        # exact after alias resolution
        if cn in geo_norm:
            mapping[csv_name] = geo_norm[cn]
            continue
        # substring: geo contains csv or vice-versa
        matches = [g for nk, g in geo_norm.items() if cn in nk or nk in cn]
        if matches:
            mapping[csv_name] = min(matches, key=len)
            continue
        # token overlap (at least 1 shared token of length ≥ 4)
        csv_tokens = {t for t in cn.split() if len(t) >= 4}
        best, best_score = None, 0
        for nk, g in geo_norm.items():
            geo_tokens = {t for t in nk.split() if len(t) >= 4}
            score = len(csv_tokens & geo_tokens)
            if score > best_score:
                best_score, best = score, g
        if best and best_score >= 1:
            mapping[csv_name] = best

    return mapping


def fmt_value(v: float, unit: str) -> str:
    """Human-readable number formatting."""
    if v is None or math.isnan(v):
        return "—"
    if abs(v) >= 1e7:
        return f"{v/1e7:.2f} Cr"
    if abs(v) >= 1e5:
        return f"{v/1e5:.2f} L"
    if abs(v) >= 1e3:
        return f"{v/1e3:.1f}K"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def infer_unit(col_name: str) -> str:
    """Guess a unit label from the column header."""
    low = col_name.lower()
    if any(k in low for k in ("area", "sq", "km", "hectare")):
        return "sq km"
    if any(k in low for k in ("household", "hh", "h.h")):
        return "households"
    if any(k in low for k in ("pop", "person", "people")):
        return "persons"
    if any(k in low for k in ("school", "hospital", "center", "centre", "facility")):
        return "units"
    if any(k in low for k in ("road", "length", "km")):
        return "km"
    return "units"


def title_from_path(csv_path: str) -> str:
    stem = Path(csv_path).stem
    return re.sub(r'[_\-]+', ' ', stem).title()


def generate_insight(data: dict, unit: str) -> str:
    """Auto-generate a one-line insight string from the indicator data."""
    if not data:
        return "No data available"
    sorted_items = sorted(data.items(), key=lambda x: x[1], reverse=True)
    top = sorted_items[0]
    top_name = top[0]
    top_val = fmt_value(top[1], unit)

    if len(sorted_items) >= 3:
        second = sorted_items[1][0]
        third = sorted_items[2][0]
        return f"{top_name} leads at {top_val} {unit}; {second} & {third} also prominent"
    elif len(sorted_items) == 2:
        second = sorted_items[1][0]
        return f"{top_name} leads at {top_val} {unit}; {second} follows"
    else:
        return f"{top_name} at {top_val} {unit}"


def infer_sector(csv_path: str, col_names: list[str]) -> str:
    """Guess a short sector label from the CSV filename or column headers."""
    stem = Path(csv_path).stem.lower()
    combined = stem + " " + " ".join(c.lower() for c in col_names)
    if any(k in combined for k in ("urban", "city", "municipal", "i&ua", "iua")):
        return "I&UA"
    if any(k in combined for k in ("agri", "farm", "crop", "rural")):
        return "Agri"
    if any(k in combined for k in ("health", "hospital", "medical")):
        return "Health"
    if any(k in combined for k in ("edu", "school", "literacy")):
        return "Edu"
    if any(k in combined for k in ("infra", "road", "bridge", "transport")):
        return "Infra"
    return "KGI"


# ── HTML template ──────────────────────────────────────────────────────────────

def build_html(
    geojson: dict,
    kgis: list[dict],
    name_prop: str,
    dashboard_title: str,
    dashboard_subtitle: str,
    theme: str,
    csv_filename: str,
    geo_filename: str,
    sector_label: str,
) -> str:

    is_dark = theme == "dark"

    # CSS variables
    if is_dark:
        css_root = """
  --bg:#0d1117; --sf:#161b22; --sf2:#1c2330;
  --bd:rgba(255,255,255,.07); --bd2:rgba(255,255,255,.13);
  --tx:#e6edf3; --mt:#8b949e;"""
        nd_fill = "#1e2736"
        stroke_normal = "rgba(255,255,255,.15)"
        stroke_hover = "rgba(255,255,255,.65)"
        tip_bg = "#1c2536"
        tip_border = "rgba(255,255,255,.15)"
        tip_shadow = "rgba(0,0,0,.6)"
        ins_bg = "rgba(255,255,255,.04)"
        sf2_bg = "rgba(255,255,255,.04)"
        footer_bg = "var(--sf)"
    else:
        css_root = """
  --bg:#f5f6f9; --sf:#ffffff; --sf2:#eef0f5;
  --bd:rgba(0,0,0,.08); --bd2:rgba(0,0,0,.13);
  --tx:#1a1f2e; --mt:#6b7592;"""
        nd_fill = "#e4e7f0"
        stroke_normal = "rgba(255,255,255,.7)"
        stroke_hover = "#ffffff"
        tip_bg = "#ffffff"
        tip_border = "rgba(0,0,0,.14)"
        tip_shadow = "rgba(0,0,0,.12)"
        ins_bg = "rgba(0,0,0,.03)"
        sf2_bg = "var(--sf2)"
        footer_bg = "var(--sf)"

    palettes = DARK_PALETTES if is_dark else PALETTES
    map_low = "#ef4444" if is_dark else "#dc2626"
    map_high = "#22c55e" if is_dark else "#86efac"

    # Build KGI JS array
    kgi_js_list = []
    for i, kgi in enumerate(kgis):
        accent, light = palettes[i % len(palettes)]
        # compute badge colours from accent
        r, g, b = int(accent[1:3], 16), int(accent[3:5], 16), int(accent[5:7], 16)
        badge_bg = f"rgba({r},{g},{b},.08)"
        # slightly darker badge text colour
        badge_c = accent
        data_js = json.dumps(kgi["data"], separators=(',', ':'))
        insight = kgi.get("insight", "Hover a state to see the value")
        nkpi_val = kgi.get("national_kpi")
        nkpi_js = "null" if nkpi_val is None else repr(float(nkpi_val))
        kgi_js_list.append(
            f'{{"code":{json.dumps(kgi["code"])},"label":{json.dumps(kgi["label"])},'
            f'"unit":{json.dumps(kgi["unit"])},"color":{json.dumps(accent)},'
            f'"light":{json.dumps(light)},"badge_bg":{json.dumps(badge_bg)},'
            f'"map_low":{json.dumps(map_low)},"map_high":{json.dumps(map_high)},'
            f'"badge_c":{json.dumps(badge_c)},"count":{kgi["count"]},'
            f'"insight":{json.dumps(insight)},'
            f'"national_kpi":{nkpi_js},'
            f'"data":{data_js}}}'
        )
    kgis_json = "[" + ",\n".join(kgi_js_list) + "]"

    geo_json_str = json.dumps(geojson, separators=(',', ':'))

    safe_title = dashboard_title.replace("&", "&amp;").replace("<", "&lt;")
    safe_subtitle = dashboard_subtitle.replace("&", "&amp;").replace("<", "&lt;")
    safe_csv = csv_filename.replace("&", "&amp;")
    safe_sector = sector_label.replace("&", "&amp;")

    n_panels = len(kgis)
    # Count regions that have data in at least one indicator
    all_data_names = set()
    for kgi in kgis:
        all_data_names.update(kgi["data"].keys())
    region_count = len(all_data_names)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title} — Choropleth Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=Playfair+Display:ital,wght@0,700;1,400&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{{css_root}
  --font:'DM Sans',system-ui,sans-serif;
}}
html{{height:100%;background:var(--bg)}}
body{{min-height:100vh;background:var(--bg);color:var(--tx);font-family:var(--font);display:flex;flex-direction:column}}

/* ── HEADER ── */
.hdr{{
  padding:1.25rem 2rem 1rem;
  background:var(--sf);border-bottom:1px solid var(--bd2);
  display:flex;align-items:center;justify-content:space-between;gap:1.5rem;flex-wrap:wrap;
}}
.hdr-left{{flex:1;min-width:0;text-align:center}}
.hdr-spacer{{flex-shrink:0}}
.tag{{
  display:inline-flex;align-items:center;gap:6px;
  font-size:10px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;
  color:#059669;background:rgba(5,150,105,.08);border:1px solid rgba(5,150,105,.22);
  padding:3px 10px;border-radius:99px;margin-bottom:8px;
}}
.tag::before{{content:'';width:5px;height:5px;border-radius:50%;background:#059669;
  animation:blink 2s ease-in-out infinite;flex-shrink:0}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
h1{{
  font-family:'Playfair Display',Georgia,serif;font-weight:700;
  font-size:clamp(1.3rem,2.2vw,1.9rem);line-height:1.15;
}}
h1 i{{font-style:italic;font-weight:400;color:#059669}}
.hdr-sub{{font-size:12px;color:var(--mt);margin-top:4px}}
.hdr-stats{{display:flex;gap:10px;flex-shrink:0}}
.scard{{
  background:{sf2_bg};border:1px solid var(--bd2);
  border-radius:10px;padding:9px 16px;text-align:center;min-width:76px;
}}
.sn{{font-size:22px;font-weight:600;line-height:1}}
.sl{{font-size:10px;color:var(--mt);text-transform:uppercase;letter-spacing:.07em;margin-top:3px}}

/* ── MAPS GRID ── */
.maps-row{{
  display:grid;
  grid-template-columns:repeat({n_panels},1fr);
  gap:0;
  background:var(--bd);
  border-top:1px solid var(--bd);
  min-height:600px;
  height:auto;
}}
.panel{{
  background:var(--sf);
  padding:1rem 1rem .75rem;
  position:relative;
  border-right:1px solid var(--bd);
  display:flex;flex-direction:column;
  min-height:550px;
  height:auto;
}}
.panel:last-child{{border-right:none}}
.panel::before{{
  content:'';position:absolute;top:0;left:0;
  width:100%;height:3px;border-radius:0;
}}

.ptop{{
  display:flex;align-items:flex-start;justify-content:space-between;
  gap:6px;margin-bottom:5px;
}}
.pinfo{{flex:1;min-width:0}}
.ptitle{{
  font-size:13px;font-weight:500;
  line-height:1.3;color:var(--tx);
}}
.pcode{{font-size:10px;color:var(--mt);font-family:monospace;margin-top:1px}}
.punit{{font-size:10px;color:var(--mt);margin-top:2px}}
.pbadge{{
  font-size:10px;font-weight:600;
  padding:2px 8px;border-radius:5px;white-space:nowrap;
  flex-shrink:0;margin-top:2px;
}}
.ins{{
  font-size:11px;color:var(--mt);
  padding:4px 8px;border-radius:5px;margin-bottom:6px;
  border-left:2px solid;background:{ins_bg};
  line-height:1.4;
}}
.nkpi{{
  font-size:11px;color:var(--mt);
  padding:4px 8px;border-radius:5px;margin-bottom:6px;
  border-left:2px solid;background:{ins_bg};
  line-height:1.4;display:flex;align-items:center;gap:5px;
}}
.nkpi b{{color:var(--tx);font-weight:500}}

/* MAP SVG — fixed height for bigscreen fit */
.mc{{width:100%;height:400px;min-height:400px;position:relative}}
.mc svg{{width:100%;height:100%;display:block}}

.state{{
  stroke:{stroke_normal};stroke-width:0.4;
  cursor:pointer;
  transition:opacity .12s,stroke .12s;
}}
.state:hover{{opacity:.82;stroke:{stroke_hover};stroke-width:1}}
.nd{{fill:{nd_fill}!important;cursor:default}}
.nd:hover{{opacity:1;stroke:{stroke_normal};stroke-width:0.4}}

/* ── LEGEND ── */
.leg{{
  display:flex;align-items:center;gap:6px;
  margin-top:6px;flex-shrink:0;
}}
.leg-t{{flex:1;height:4px;border-radius:2px}}
.leg-l{{font-size:9px;color:var(--mt)}}

/* ── TOOLTIP ── */
#tip{{
  position:fixed;
  background:{tip_bg};border:1px solid {tip_border};
  border-radius:9px;padding:8px 12px;
  font-size:12px;pointer-events:none;
  z-index:1000;display:none;max-width:200px;
  box-shadow:0 4px 20px {tip_shadow};
}}
#tip .ts{{font-weight:600;font-size:13px;margin-bottom:2px;color:var(--tx)}}
#tip .tv{{color:var(--mt);font-size:11px}}
#tip .tv b{{color:var(--tx);font-weight:500}}

/* ── FOOTER ── */
footer{{
  padding:8px 2rem;
  border-top:1px solid var(--bd);
  font-size:10.5px;color:var(--mt);
  background:{footer_bg};
  display:flex;gap:14px;flex-wrap:wrap;
}}
footer b{{color:var(--tx);font-weight:500}}

/* ── RESPONSIVE BREAKPOINTS ── */
@media(max-width:900px){{
  .maps-row{{grid-template-columns:repeat(2,1fr)}}
  .panel{{border-bottom:1px solid var(--bd)}}
}}
@media(max-width:500px){{
  .maps-row{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>

<div class="hdr">
  <div class="hdr-stats">
    <div class="scard"><div class="sn" style="color:#2563eb">{region_count}</div><div class="sl">Regions</div></div>
    <div class="scard"><div class="sn" style="color:#dc2626">{n_panels}</div><div class="sl">Indicators</div></div>
  </div>
  <div class="hdr-left">
    <div class="tag">KGI Insights</div>
    <h1>{safe_title}</h1>
    {f'<p class="hdr-sub">{safe_subtitle}</p>' if safe_subtitle else ''}
  </div>
  <div class="hdr-spacer"></div>
</div>

<div class="maps-row" id="grid"></div>

<div id="tip">
  <div class="ts" id="ts"></div>
  <div class="tv" id="tk"></div>
  <div class="tv" id="tv"></div>
</div>

<footer>
  Source: <b>{safe_csv}</b> &nbsp;&middot;&nbsp;
  Map: <b>{geo_filename}</b> &nbsp;&middot;&nbsp;
  Light grey = no data &nbsp;&middot;&nbsp;
  Color scale: low (red) to high (green), log-proportional
</footer>

<script>
const GEO = {geo_json_str};
const NAME_PROP = {json.dumps(name_prop)};
const KGIS = {kgis_json};

/* ── formatting helper ── */
function fmt(v, unit) {{
  if (v == null || isNaN(v)) return '—';
  const abs = Math.abs(v);
  if (unit === 'sq km') {{
    if (abs >= 1e5)  return (v/1e5).toFixed(1)+' L sq km';
    if (abs >= 1e3)  return (v/1e3).toFixed(1)+'K sq km';
    return Math.round(v)+' sq km';
  }}
  if (abs >= 1e7) return (v/1e7).toFixed(1)+' Cr';
  if (abs >= 1e5) return (v/1e5).toFixed(1)+' L';
  if (abs >= 1e3) return (v/1e3).toFixed(0)+'K';
  return v.toLocaleString(undefined, {{maximumFractionDigits:2}});
}}

/* ── tooltip ── */
const tipEl  = document.getElementById('tip');
const tsEl   = document.getElementById('ts');
const tkEl   = document.getElementById('tk');
const tvEl   = document.getElementById('tv');

function showTip(e, name, kgi) {{
  const v = kgi.data[name] ?? null;
  tsEl.textContent = name;
  tkEl.textContent = kgi.label;
  tvEl.innerHTML = v != null
    ? 'Value: <b>' + fmt(v, kgi.unit) + ' ' + kgi.unit + '</b>'
    : '<span style="color:#9ca3af">No data</span>';
  tipEl.style.display = 'block';
  moveTip(e);
}}

function moveTip(e) {{
  tipEl.style.left = Math.min(e.clientX + 12, window.innerWidth - 210) + 'px';
  tipEl.style.top  = (e.clientY - 10) + 'px';
}}

document.addEventListener('mousemove', moveTip);

/* ── build dashboard ── */
function buildDash() {{
  const features = GEO.features;
  const grid = document.getElementById('grid');

  KGIS.forEach((kgi, idx) => {{
    const vals = Object.values(kgi.data).filter(v => v > 0);
    if (!vals.length) return;

    const logMin = Math.log10(Math.min(...vals) || 1);
    const logMax = Math.log10(Math.max(...vals) || 1);

    /* panel */
    const panel = document.createElement('div');
    panel.className = 'panel';
    panel.style.setProperty('--acc', kgi.color);
    panel.innerHTML =
      '<style>.panel:nth-child(' + (idx+1) + ')::before{{background:' + kgi.color + '}}</style>' +
      '<div class="ptop">' +
        '<div class="pinfo">' +
          '<div class="ptitle">' + kgi.label + '</div>' +
          '<div class="pcode">' + kgi.code + '</div>' +
          '<div class="punit">Unit: ' + kgi.unit + '</div>' +
        '</div>' +
        '<div class="pbadge" style="background:' + kgi.badge_bg + ';color:' + kgi.badge_c + '">' +
          vals.length + ' states</div>' +
      '</div>' +
      '<div class="ins" style="border-color:' + kgi.color + '">' + kgi.insight + '</div>' +
      (kgi.national_kpi != null
        ? '<div class="nkpi" style="border-color:' + kgi.color + '">\U0001F3AF National KPI: <b>' + fmt(kgi.national_kpi, kgi.unit) + ' ' + kgi.unit + '</b></div>'
        : '') +
      '<div class="mc" id="mc' + idx + '"></div>' +
      '<div class="leg">' +
        '<div class="leg-l">Low</div>' +
        '<div class="leg-t" style="background:linear-gradient(to right,' + kgi.map_low + ',' + kgi.map_high + ')"></div>' +
        '<div class="leg-l">High</div>' +
      '</div>';
    grid.appendChild(panel);

    /* svg */
    const mc  = panel.querySelector('#mc' + idx);
    const W   = 340, H = 320;
    const svg = d3.select(mc).append('svg')
      .attr('viewBox', '0 0 ' + W + ' ' + H)
      .attr('preserveAspectRatio', 'xMidYMid meet');

    const proj = d3.geoMercator()
      .fitSize([W - 6, H - 6], {{type: 'FeatureCollection', features}});
    const tr = proj.translate();
    proj.translate([tr[0] + 3, tr[1] + 3]);
    const path = d3.geoPath().projection(proj);

    /* log color scale */
    const cs = d3.scaleSequential()
      .domain([logMin, logMax])
      .interpolator(d3.interpolateRgb(kgi.map_low, kgi.map_high));

    svg.selectAll('path')
      .data(features)
      .join('path')
      .attr('class', d => {{
        const n = d.properties[NAME_PROP] || '';
        return 'state' + (kgi.data[n] == null ? ' nd' : '');
      }})
      .attr('fill', d => {{
        const n = d.properties[NAME_PROP] || '';
        const v = kgi.data[n];
        if (v == null || v <= 0) return '{nd_fill}';
        return cs(Math.log10(v));
      }})
      .attr('d', path)
      .on('mouseover', (e, d) => showTip(e, d.properties[NAME_PROP] || '', kgi))
      .on('mouseout', () => {{ tipEl.style.display = 'none'; }});
  }});
}}

buildDash();
</script>
</body>
</html>"""

    return html


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate a choropleth HTML dashboard from a GeoJSON + CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("geojson_path", help="Path to GeoJSON file")
    parser.add_argument("csv_path", help="Path to CSV data file")
    parser.add_argument(
        "--name-prop", default=None,
        help="GeoJSON property holding the region name (auto-detected if omitted)"
    )
    parser.add_argument(
        "--title", default=None,
        help="Dashboard heading (default: derived from CSV filename)"
    )
    parser.add_argument(
        "--subtitle", default="",
        help="Sub-heading text shown below the title"
    )
    parser.add_argument(
        "--out", default=None,
        help="Output HTML path (default: <csv_stem>_dashboard.html)"
    )
    parser.add_argument(
        "--theme", default="light", choices=["light", "dark"],
        help="Color theme: 'light' (default) or 'dark'"
    )
    args = parser.parse_args()

    # ── load GeoJSON ──────────────────────────────────────────────────────────
    geojson_path = Path(args.geojson_path)
    if not geojson_path.exists():
        sys.exit(f"ERROR: GeoJSON file not found: {geojson_path}")
    print(f"Loading GeoJSON: {geojson_path} …", flush=True)
    with open(geojson_path, encoding="utf-8") as f:
        geojson = json.load(f)
    if geojson.get("type") != "FeatureCollection":
        sys.exit("ERROR: GeoJSON must be a FeatureCollection.")
    n_features = len(geojson.get("features", []))
    print(f"  {n_features} features loaded.")

    # ── detect name property ──────────────────────────────────────────────────
    name_prop = args.name_prop or auto_detect_name_prop(geojson)
    geo_names = [
        f["properties"].get(name_prop, "") for f in geojson["features"]
        if f.get("properties")
    ]
    geo_names = [n for n in geo_names if n]
    print(f"  Using name property: '{name_prop}'  ({len(geo_names)} named regions)")

    # ── load CSV ──────────────────────────────────────────────────────────────
    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        sys.exit(f"ERROR: CSV file not found: {csv_path}")
    print(f"Loading CSV: {csv_path} …", flush=True)
    df = pd.read_csv(csv_path)
    if df.shape[1] < 2:
        sys.exit("ERROR: CSV must have at least 2 columns.")
    if df.shape[1] < 5:
        print(f"  WARNING: found only {df.shape[1]-1} indicator column(s); expected up to 4.")

    state_col  = df.columns[0]
    ind_cols   = list(df.columns[1:5])   # up to 4 indicators
    print(f"  State column : '{state_col}'")
    print(f"  Indicators   : {ind_cols}")

    # ── fuzzy-match state names ───────────────────────────────────────────────
    csv_states = df[state_col].dropna().astype(str).tolist()
    name_map   = build_fuzzy_map(geo_names, csv_states)
    matched    = sum(1 for v in name_map.values() if v)
    print(f"  Matched {matched}/{len(csv_states)} CSV states to GeoJSON regions.")
    unmatched  = [s for s in csv_states if s not in name_map]
    if unmatched:
        print(f"  Unmatched (will appear grey): {unmatched}")

    # ── build KGI data objects ────────────────────────────────────────────────
    kgis = []
    for col in ind_cols:
        data = {}
        for _, row in df.iterrows():
            csv_name = str(row[state_col]) if pd.notna(row[state_col]) else None
            if not csv_name:
                continue
            geo_name = name_map.get(csv_name)
            if not geo_name:
                continue
            raw = row[col]
            if pd.isna(raw):
                continue
            try:
                v = float(raw)
            except (ValueError, TypeError):
                continue
            if v > 0:                      # skip zeros / negatives
                data[geo_name] = v
        unit  = infer_unit(col)
        count = len(data)
        insight = generate_insight(data, unit)
        print(f"  {col}: {count} regions with data")
        kgis.append({
            "code":  col,
            "label": col,            # header used as-is
            "unit":  unit,
            "data":  data,
            "count": count,
            "insight": insight,
        })

    if not kgis:
        sys.exit("ERROR: No usable indicator data found.")

    # ── dashboard metadata ─────────────────────────────────────────────────────
    dashboard_title    = args.title or title_from_path(str(csv_path))
    dashboard_subtitle = args.subtitle
    sector_label       = infer_sector(str(csv_path), ind_cols)

    # ── output path ────────────────────────────────────────────────────────────
    out_path = Path(args.out) if args.out else csv_path.with_name(csv_path.stem + "_dashboard.html")

    # ── render HTML ────────────────────────────────────────────────────────────
    print(f"\nBuilding HTML dashboard …", flush=True)
    html = build_html(
        geojson           = geojson,
        kgis              = kgis,
        name_prop         = name_prop,
        dashboard_title   = dashboard_title,
        dashboard_subtitle= dashboard_subtitle,
        theme             = args.theme,
        csv_filename      = csv_path.name,
        geo_filename      = geojson_path.name,
        sector_label      = sector_label,
    )

    out_path.write_text(html, encoding="utf-8")
    size_kb = out_path.stat().st_size // 1024
    print(f"✓ Saved: {out_path}  ({size_kb} KB)")
    print(f"\nOpen in any browser:")
    print(f"  file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
