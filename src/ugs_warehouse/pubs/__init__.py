"""Pubs producer — UGS publications → COG + footprints + units + STAC, on the shared core.

Keyed by `series_id` (e.g. M-299DM), sourced from the live publications database + the geologic-map footprints. Ported from the ugs-geolmap-cog-poc; emits
into the `ugs-publications` collection of the one catalog. See docs/ARCHITECTURE.md.
"""
from __future__ import annotations
