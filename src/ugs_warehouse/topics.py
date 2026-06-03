"""Topic registry — one entry per `{schema}.{topic}_current` Postgres table the warehouse owns.

Topics drive both ingest (one Iceberg + GeoParquet + PMTiles + STAC set per topic)
and per-app PMTiles bundle composition (a topic may appear in multiple apps).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    layer: str   # full `_current` table name; also the MapLibre source-layer name
    schema: str  # Postgres / dbt mart schema (= warehouse partition root)

    @property
    def stem(self) -> str:
        """Bare topic name, suffix stripped."""
        return self.layer.removesuffix("_current")

    @property
    def fqn(self) -> str:
        return f"{self.schema}.{self.layer}"


def _t(layer: str, schema: str) -> Topic:
    return Topic(layer=layer, schema=schema)


REGISTRY: dict[str, Topic] = {t.layer: t for t in [
    # --- hazards ---
    _t("hazards_qfaults_current",              "hazards"),
    _t("hazards_surfacefaultrupture_current",  "hazards"),
    _t("liquefaction_current",                 "hazards"),
    _t("landslidesusceptibility_current",      "hazards"),
    _t("landslideinventory_current",           "hazards"),
    _t("landslidelegacy_current",              "hazards"),
    _t("rockfall_current",                     "hazards"),
    _t("floodanddebrisflow_current",           "hazards"),
    _t("groundshaking_current",                "hazards"),
    _t("alluvialfan_current",                  "hazards"),
    _t("collapsiblesoil_current",              "hazards"),
    _t("corrosivesoilrock_current",            "hazards"),
    _t("earthfissure_current",                 "hazards"),
    _t("erosionhazardzone_current",            "hazards"),
    _t("expansivesoilrock_current",            "hazards"),
    _t("karstfeatures_current",                "hazards"),
    _t("pipinganderosion_current",             "hazards"),
    _t("radonsusceptibility_current",          "hazards"),
    _t("salttectonicsdeformation_current",     "hazards"),
    _t("shallowbedrock_current",               "hazards"),
    _t("shallowgroundwater_current",           "hazards"),
    _t("solublesoilandrock_current",           "hazards"),
    _t("windblownsand_current",                "hazards"),
    # --- emp (energy + minerals) ---
    _t("enmin_geophysics_mtstations_current",                "emp"),
    _t("enmin_geophysics_pacesgravity_current",              "emp"),
    _t("enmin_geophysics_tem_current",                       "emp"),
    _t("enmin_geophysics_ugsgravity_current",                "emp"),
    _t("enmin_geothermal_ingenious_springfeatures_current",  "emp"),
    _t("enmin_geothermal_ingenious_wellfeatures_current",    "emp"),
    _t("geothermal_deepsedbasin_current",                    "emp"),
    _t("geothermal_kgra_current",                            "emp"),
    _t("geothermal_potentialresourcearea_current",           "emp"),
    _t("geothermal_utgeothermaluses_current",                "emp"),
    _t("mart_geophysics_heatflowedwards_source_current",     "emp"),
    _t("mart_geothermal_wellsandsprings_current",            "emp"),
    # --- gen_gis (shared reference) ---
    _t("studyareas_current", "gen_gis"),
]}


# Per-app PMTiles bundle composition. A topic may appear in multiple apps.
APPS: dict[str, list[str]] = {
    "hazards": [layer for layer, t in REGISTRY.items() if t.schema == "hazards"],
    "geophysics": [
        "enmin_geophysics_mtstations_current",
        "enmin_geophysics_pacesgravity_current",
        "enmin_geophysics_tem_current",
        "enmin_geophysics_ugsgravity_current",
        "enmin_geothermal_ingenious_springfeatures_current",
        "enmin_geothermal_ingenious_wellfeatures_current",
        "geothermal_deepsedbasin_current",
        "geothermal_kgra_current",
        "geothermal_potentialresourcearea_current",
        "geothermal_utgeothermaluses_current",
        "mart_geophysics_heatflowedwards_source_current",
        "mart_geothermal_wellsandsprings_current",
        "hazards_qfaults_current",
    ],
    "carbonstorage":  ["hazards_qfaults_current"],
    "subsurface":     ["hazards_qfaults_current"],
    "wetlandplants":  ["studyareas_current"],
    "minerals":       [],
    "wetlands":       [],
}


def all_topics() -> list[Topic]:
    return list(REGISTRY.values())


def topics_for_app(app: str) -> list[Topic]:
    return [REGISTRY[name] for name in APPS.get(app, []) if name in REGISTRY]
