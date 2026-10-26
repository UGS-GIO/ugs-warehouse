// The phone sheet's shared chrome: the map's own sheet (map-shell.tsx) and What's here drag, snap and
// look the same. Base UI's Drawer does the drag, flick and scroll handoff.
import type { MouseEvent } from "react";

// No text selection, tap highlight or callout menu on a long press of the sheet's chrome.
export const GRAB = "select-none [-webkit-touch-callout:none] [-webkit-tap-highlight-color:transparent]";
export const noMenu = (e: MouseEvent) => e.preventDefault();

/** The Drawer.Popup: follows the snap point and the finger, and settles with a short ease. */
export const SHEET_POPUP = "pointer-events-auto relative flex w-full flex-col rounded-t-2xl border-t border-border bg-background shadow-2xl outline-none [padding-bottom:max(0px,calc(var(--drawer-snap-point-offset)+var(--drawer-swipe-movement-y)))] [transform:translateY(calc(var(--drawer-snap-point-offset)+var(--drawer-swipe-movement-y)))] transition-transform duration-300 ease-out data-swiping:duration-0 motion-reduce:transition-none";
