// Our own map UI as real maplibre controls. Two absolutely-positioned cards in one corner don't
// know each other's height, which is how a stock control (geolocate, zoom) ended up under the
// basemap switch. A corner container flows instead. Same shape as the soil-water viewer's version.
import { createPortal } from "react-dom";
import { useState, type ReactNode } from "react";
import { type ControlPosition, useControl } from "react-map-gl/maplibre";

class PortalControl {
  readonly container: HTMLDivElement;

  constructor(className: string) {
    this.container = document.createElement("div");
    // `ugs-ctrl` restates maplibre's corner layout (index.css) without the chrome it puts on
    // control buttons — that chrome outranks the app's own hover and pressed styles.
    this.container.className = `ugs-ctrl ${className}`;
  }

  onAdd(): HTMLDivElement {
    return this.container;
  }

  onRemove(): void {
    this.container.remove();
  }
}

/** Renders `children` into a maplibre control container. Controls stack in the order they're
 *  added, so JSX order is corner order. `className` styles the container (width, spacing). */
export function MapControl({ position, className = "", children }: {
  position: ControlPosition; className?: string; children: ReactNode;
}) {
  const [control] = useState(() => new PortalControl(className));
  useControl(() => control, { position });
  return createPortal(children, control.container);
}
