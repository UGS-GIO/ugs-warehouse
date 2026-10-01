// User guide — renders docs/USER_GUIDE.md (the single source of truth) as a page. The OGC API
// base is substituted live from the same VITE_FEATURES_BASE the rest of the viewer uses, so the
// guide's OGC examples show the real URL when one is configured (else the {OGC_API_BASE} placeholder).
import guideMd from "../../../docs/USER_GUIDE.md?raw";
import { MarkdownPage } from "./markdown-page";
import { FEATURES_BASE } from "@/stac";

// The guide's screenshots, bundled by path so `img/...` links resolve here as on the docs site.
const IMAGES = import.meta.glob<string>("../../../docs/img/**/*.png", { eager: true, query: "?url", import: "default" });

export function Guide() {
  const source = FEATURES_BASE
    ? guideMd.replaceAll("{OGC_API_BASE}", FEATURES_BASE)
    : guideMd;
  // Drop MkDocs `{ width=... }` attributes, which react-markdown would print as text.
  const withImages = source.replace(/\]\((img\/[^)]+)\)(\{[^}]*\})?/g,
    (_, path: string) => `](${IMAGES[`../../../docs/${path}`] ?? path})`);
  return <MarkdownPage source={withImages} />;
}
