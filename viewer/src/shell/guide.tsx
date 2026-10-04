// User guide — renders docs/USER_GUIDE.md (the single source of truth) as a page, with the developer
// reference (`developers`) after it.
import type { ReactNode } from "react";

import guideMd from "../../../docs/USER_GUIDE.md?raw";
import { MarkdownPage } from "./markdown-page";

// The guide's screenshots, bundled by path so `img/...` links resolve here as on the docs site.
const IMAGES = import.meta.glob<string>("../../../docs/img/**/*.png", { eager: true, query: "?url", import: "default" });

export function Guide({ developers }: { developers?: ReactNode }) {
  // Drop MkDocs `{ width=... }` attributes, which react-markdown would print as text.
  const withImages = guideMd.replace(/\]\((img\/[^)]+)\)(\{[^}]*\})?/g,
    (_, path: string) => `](${IMAGES[`../../../docs/${path}`] ?? path})`);
  const toc = guideMd.split("\n").filter((l) => l.startsWith("## ")).map((l) => l.slice(3).trim());
  if (developers) toc.push("For developers");
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
      <div className="flex min-w-0 flex-1 flex-col items-center">
        <MarkdownPage source={withImages} />
        {developers}
      </div>
      <div aria-hidden className="hidden w-56 shrink-0 lg:block" />
    </div>
  );
}

/** A heading's id as rehype-slug makes it, so the section nav links land. */
function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9 -]/g, "").trim().replace(/\s+/g, "-");
}
