// CSS custom properties set from React `style`, typed so no cast is needed to set them.
import "react";

declare module "react" {
  interface CSSProperties {
    /** Height of the collapsed bottom sheet on phones; lifts the map's bottom controls (index.css). */
    "--peek"?: string;
  }
}
