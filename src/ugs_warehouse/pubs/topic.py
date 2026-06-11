"""Derive a pub's subject TOPIC from its title (+ keywords).

UGS pubs have no topic field, but the title is descriptive. One shared classifier so the
STAC items and the footprints tag the SAME `ugs:topic`, and viewers can filter formation
maps vs thematic ones. Rules are ordered — a specific theme beats the generic "geologic"
catch-all (e.g. "Geologic map of the X coal field" → mineral-energy). Heuristic, ~90%.
Ported from ugs-geolmap-cog-poc/catalog/topic.py.
"""
from __future__ import annotations

import re

# (topic, label, regex) — first match wins; specific themes before generic geology.
RULES = [
    ("geophysics",     "Geophysics",      r"gravity|aeromagnetic|\bmagnetic\b|seismic|geophysic"),
    ("hazards",        "Hazards",         r"hazard|landslide|liquefaction|\bflood|earthquake|debris.?flow|rockfall|surface.fault"),
    ("mineral-energy", "Mineral / Energy", r"mineral resource|mineral potential|\bmining\b|\bore\b|uranium|\bcoal\b|oil and gas|gas field|petroleum|geothermal|potash|tar sand|energy"),
    ("hydro",          "Hydro",           r"hydrogeolog|ground.?water|aquifer|water resource|hydrolog|wetland"),
    ("surficial",      "Surficial",       r"surficial"),
    ("geologic",       "Geologic (bedrock)", r"geologic|geology|bedrock"),
]
LABELS = {t: lbl for t, lbl, _ in RULES}
LABELS["other"] = "Other"


def classify(title: str | None, keywords: str | None = "") -> str:
    text = f"{title or ''} {keywords or ''}".lower()
    for topic, _label, pattern in RULES:
        if re.search(pattern, text):
            return topic
    return "other"
