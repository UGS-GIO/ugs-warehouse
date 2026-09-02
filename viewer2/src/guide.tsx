// User guide — renders docs/USER_GUIDE.md (the single source of truth) as a page. The OGC API
// base is substituted live from the same VITE_FEATURES_BASE the rest of the viewer uses, so the
// guide's OGC examples show the real URL when one is configured (else the {OGC_API_BASE} placeholder).
import guideMd from "../../docs/USER_GUIDE.md?raw";
import { MarkdownPage } from "./markdown-page";
import { FEATURES_BASE } from "./stac";

export function Guide() {
  const source = FEATURES_BASE
    ? guideMd.replaceAll("{OGC_API_BASE}", FEATURES_BASE)
    : guideMd;
  return <MarkdownPage source={source} />;
}
