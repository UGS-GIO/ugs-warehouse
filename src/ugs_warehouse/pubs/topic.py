"""Derive a pub's subject TOPIC from its title (+ keywords).

UGS pubs have no topic field, but the title is descriptive. One shared classifier so the
STAC items and the footprints tag the SAME `ugs:topic`, and viewers can filter formation
maps vs thematic ones. Rules are ordered — a specific theme beats the generic "geologic"
catch-all (e.g. "Geologic map of the X coal field" → mineral-energy). Heuristic, ~90%.
Ported from ugs-geolmap-cog-poc/catalog/topic.py.
"""
from __future__ import annotations

import re

# (topic, label, regex) — first match wins; specific themes before generic geology. Beyond the
# geologic-map themes, the later rules cover Survey Notes editorial content (paleontology, education,
# geosites, history, ice-age/lakes, caves) so articles classify into something meaningful instead of
# collapsing to "geologic" / "mineral-energy".
RULES = [
    ("geophysics",     "Geophysics",      r"gravity|aeromagnetic|\bmagnetic\b|seismic|geophysic"),
    ("hazards",        "Hazards",         r"hazard|landslide|liquefaction|\bflood|earthquake|debris.?flow|rockfall|surface.fault|radon|expansive soil"),
    ("mineral-energy", "Mineral / Energy", r"mineral resource|mineral potential|\bmining\b|\bore\b|uranium|\bcoal\b|oil and gas|gas field|petroleum|geothermal|potash|tar sand|\blithium\b|critical mineral|energy"),
    ("paleontology",   "Paleontology",    r"paleontolog|\bfossil|dinosaur|trilobite|trackway|\bbones?\b|ammonite|\bmammoth|ice.age animal"),
    ("glacial",        "Glacial / Ice Age", r"glacier|glacial|ice age|pleistocene|lake bonneville|moraine"),
    ("hydro",          "Water",           r"hydrogeolog|ground.?water|aquifer|water resource|hydrolog|wetland|spring\b|\bbrine\b|great salt lake"),
    ("caves",          "Caves / Karst",   r"\bcave|karst|sinkhole|speleo|\bcavern"),
    ("education",      "Education",       r"teacher|classroom|\beducation|glad you asked|curriculum|\bstudent|geologic time|rock cycle"),
    ("geosites",       "Geosites / Parks", r"state park|national (park|monument)|geosite|scenic|hoodoo|geologic wonder|rock.?hound|geotour"),
    ("history",        "History",         r"\bhistory\b|historic|pioneer|heritage|mining camp|ghost town"),
    ("surficial",      "Surficial",       r"surficial"),
    ("geologic",       "Geologic (bedrock)", r"geologic|geology|bedrock|stratigraph|formation\b|quadrangle|\bmapping\b|geologic map"),
]
LABELS = {t: lbl for t, lbl, _ in RULES}
LABELS["other"] = "Other"


def classify(title: str | None, keywords: str | None = "") -> str:
    text = f"{title or ''} {keywords or ''}".lower()
    for topic, _label, pattern in RULES:
        if re.search(pattern, text):
            return topic
    return "other"
