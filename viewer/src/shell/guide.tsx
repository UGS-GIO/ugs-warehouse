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
  const toc = guideMd.split("\n").filter((l) => l.startsWith("## ")).map((l) => l.slice(3).trim());
  // Section nav pinned to the left gutter; the spacer on the right keeps the text centred.
  return (
    <div className="mx-auto flex w-full max-w-[100rem] gap-10 px-4 sm:px-8">
      <nav aria-label="On this page" className="sticky top-6 hidden h-fit w-56 shrink-0 py-8 lg:block">
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">On this page</p>
        <ul className="space-y-1 border-l border-border">
          {toc.map((t) => (
            <li key={t}>
              <a href={`#${slug(t)}`}
                className="-ml-px block border-l border-transparent py-0.5 pl-3 text-sm text-muted-foreground hover:border-primary hover:text-foreground">
                {t}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex min-w-0 flex-1 justify-center">
        <MarkdownPage source={withImages} />
      </div>
      <div aria-hidden className="hidden w-56 shrink-0 lg:block" />
    </div>
  );
}

/** A heading's id as rehype-slug makes it, so the section nav links land. */
function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9 -]/g, "").trim().replace(/\s+/g, "-");
}
