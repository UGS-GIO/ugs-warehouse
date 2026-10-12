// A bound ugs-styles fragment can be entirely valid and still draw NOTHING at the zoom the map
// opens at. PLSS sections are gated to z≥11.13 — faithful to the source SLD's MaxScaleDenominator,
// and right, since 81,544 section polygons at z6 is a smear — while a statewide fit lands near z6.
// MapLibre then activates no layer and never requests a tile, so the map is blank and looks
// identical to an unstyled (or broken) layer. That is how UGS-GIO/ugs-styles#34 got filed as a
// missing-style bug when the styles were fine.
//
// This derives the zoom window a render can actually draw in, so the UI can say "zoom in" instead
// of showing empty space. Only layers that draw the feature's GEOMETRY count: a text-only `symbol`
// layer labels geometry something else has to draw, so its own minzoom is just the label gate. Same
// predicate ugs-styles enforces at build time (its src/layers.ts).
import { useEffect, useRef, useState, type RefObject } from "react";
import type { MapRef } from "react-map-gl/maplibre";

export type Gate = { min: number; max: number };
export type GateDir = "in" | "out";   // which way the user has to zoom to see the layer

// MapLibre's ceiling — the effective maxzoom when a layer declares none.
export const MAX_ZOOM = 24;

// Scanned plates decode client-side, and zoomed out each one decodes an overview across the whole
// viewport: 12 of them cost 1092MB of heap at z5, 61MB at z10. Unreadable that far out anyway.
export const COG_GATE: Gate = { min: 8, max: MAX_ZOOM };

const DRAW_TYPES = new Set(["fill", "line", "circle", "fill-extrusion", "heatmap"]);

const drawsGeometry = (l: Record<string, unknown>): boolean => {
  const type = typeof l.type === "string" ? l.type : "";
  if (DRAW_TYPES.has(type)) return true;
  // An icon-bearing symbol layer DOES draw (the sprite renders — UCRC wells by-boxtype).
  const layout = (l.layout ?? {}) as Record<string, unknown>;
  return type === "symbol" && layout["icon-image"] != null;
};

const num = (v: unknown, fallback: number): number =>
  typeof v === "number" && Number.isFinite(v) ? v : fallback;

/**
 * The zoom window in which a style fragment draws geometry, or null when nothing in it draws at all
 * (ugs-styles' own build gate owns that case, so there's nothing useful to tell the user here).
 *
 * The window is the union of the drawing layers' ranges. A style whose layers left a HOLE — one
 * drawing 0–5 and another 15–24 — would report 0–24 and under-warn; no published style does that,
 * and modelling it would buy nothing but complexity.
 */
export function gateOf(layers?: Record<string, unknown>[] | null): Gate | null {
  const drawing = (layers ?? []).filter(drawsGeometry);
  if (!drawing.length) return null;
  return {
    min: Math.min(...drawing.map((l) => num(l.minzoom, 0))),
    max: Math.max(...drawing.map((l) => num(l.maxzoom, MAX_ZOOM))),
  };
}

/** Which way the user must zoom for `gate` to draw at zoom `z` — null when it already does. */
export const gateDir = (z: number, gate: Gate | null): GateDir | null =>
  !gate ? null : z < gate.min ? "in" : z > gate.max ? "out" : null;

/**
 * The zoom that brings `gate` into range. The gate's own bound, exactly — `minzoom` is inclusive,
 * so easing to it is the smallest move that works. Callers ease the ZOOM ONLY and keep the current
 * centre: fitting the layer's bounds instead would drop a statewide layer onto one arbitrary
 * section, which reads worse than the blank map it replaced.
 */
export const gateZoom = (gate: Gate, dir: GateDir): number => (dir === "in" ? gate.min : gate.max);

const sameDirs = (a: Record<string, GateDir>, b: Record<string, GateDir>): boolean => {
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => a[k] === b[k]);
};

/**
 * Which of `entries` can't draw at the map's CURRENT zoom, as id → the direction to zoom.
 *
 * Zoom is read off the live map on `move` rather than held in React state: the result only changes
 * when a layer crosses its gate, so returning the previous object on no-change means a zoom gesture
 * re-renders the map subtree on an actual transition instead of every frame.
 */
export function useGatedOut(
  mapRef: RefObject<MapRef | null>,
  entries: { id: string; gate: Gate | null }[],
  ready: boolean,
): Record<string, GateDir> {
  const [gated, setGated] = useState<Record<string, GateDir>>({});
  // Latest entries without making them an effect dep — `sig` is what actually changed.
  const entriesRef = useRef(entries);
  entriesRef.current = entries;
  const sig = entries.map((e) => `${e.id}:${e.gate?.min ?? ""}:${e.gate?.max ?? ""}`).join("|");

  useEffect(() => {
    const map = ready ? mapRef.current?.getMap() : undefined;
    if (!map) { setGated({}); return; }
    const check = () => {
      const z = map.getZoom();
      const next: Record<string, GateDir> = {};
      for (const e of entriesRef.current) {
        const d = gateDir(z, e.gate);
        if (d) next[e.id] = d;
      }
      setGated((prev) => (sameDirs(prev, next) ? prev : next));
    };
    check();
    map.on("move", check);
    return () => { map.off("move", check); };
  }, [sig, ready, mapRef]);

  return gated;
}

/**
 * Collapse the layers hidden in one direction into a single notice: what to name, and the one move
 * that reveals ALL of them. Zooming in targets the DEEPEST gate in the group — the shallowest would
 * leave some of the named layers still blank, which is the confusion this whole thing exists to end.
 */
export function groupGate(
  gated: { title: string; gate: Gate | null }[],
  dir: GateDir,
): { gate: Gate; subject: string } | null {
  if (!gated.length) return null;
  const gate: Gate = dir === "in"
    ? { min: Math.max(...gated.map((g) => g.gate?.min ?? 0)), max: MAX_ZOOM }
    : { min: 0, max: Math.min(...gated.map((g) => g.gate?.max ?? MAX_ZOOM)) };
  return { gate, subject: gated.map((g) => g.title).join(", ") };
}

/** Single-layer form (the item preview, which shows one render at a time). */
export const useGateDir = (
  mapRef: RefObject<MapRef | null>,
  gate: Gate | null,
  ready: boolean,
): GateDir | null => useGatedOut(mapRef, [{ id: "_", gate }], ready)["_"] ?? null;

/**
 * The on-map notice. Lives over the map because that is where the user is looking at blank space.
 * `subject` names what's hidden when there's more than one candidate (the overlay map lists layer
 * titles); the item preview shows one render at a time and needs no subject.
 *
 * The zoom-in copy rounds UP: a gate of 11.13 reads as "below zoom 12", which is true. Rounding
 * down would name a zoom that still draws nothing. The zoom-out copy carries no number — a maxzoom
 * gate can't be stated as a clean integer without misstating the fractional bound, and none of the
 * published styles uses one.
 */
export function ZoomGateNotice({ gate, dir, subject, onZoom }: {
  gate: Gate; dir: GateDir; subject?: string; onZoom: () => void;
}) {
  return (
    <div className="flex max-w-[24rem] items-center gap-2 rounded-md border border-border bg-card/95 px-2.5 py-1.5 text-xs shadow backdrop-blur-sm">
      <span className="text-foreground">
        {dir === "in" ? `Hidden below zoom ${Math.ceil(gate.min)}` : "Hidden at this zoom"}
        {subject && <span className="text-muted-foreground"> · {subject}</span>}
      </span>
      <button onClick={onZoom}
        className="shrink-0 rounded bg-primary px-2 py-0.5 font-semibold text-primary-foreground hover:opacity-90"
        title={dir === "in" ? `Draws from zoom ${gate.min.toFixed(2)} — keeps the current centre`
                            : `Draws below zoom ${gate.max.toFixed(2)} — keeps the current centre`}>
        {dir === "in" ? "Zoom in" : "Zoom out"}
      </button>
    </div>
  );
}
