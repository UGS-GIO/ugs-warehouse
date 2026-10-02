// Render a markdown string as a styled page — so info pages (user guide, etc.) are just markdown,
// no hand-built React/Tailwind. GFM tables + fenced code + heading anchors. Styling is a single
// self-contained className (no @tailwindcss/typography dependency), matching the app's tokens.
import ReactMarkdown from "react-markdown";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";

const PROSE = [
  "w-full min-w-0 py-6 max-w-[62rem] text-foreground",
  // scroll-mt-28: anchor jumps (#section) land BELOW the sticky header instead of under it.
  "[&_h1]:text-2xl [&_h1]:font-bold [&_h1]:mt-2 [&_h1]:mb-3 [&_h1]:scroll-mt-28",
  "[&_h2]:text-xl [&_h2]:font-semibold [&_h2]:mt-8 [&_h2]:mb-2 [&_h2]:border-b [&_h2]:border-border [&_h2]:pb-1 [&_h2]:scroll-mt-28",
  "[&_h3]:text-base [&_h3]:font-semibold [&_h3]:mt-5 [&_h3]:mb-1.5 [&_h3]:scroll-mt-28",
  "[&_p]:my-2.5 [&_p]:leading-relaxed",
  "[&_ul]:my-2.5 [&_ul]:list-disc [&_ul]:pl-6 [&_ol]:my-2.5 [&_ol]:list-decimal [&_ol]:pl-6 [&_li]:my-1",
  "[&_a]:text-primary [&_a]:underline [&_a:hover]:opacity-80",
  "[&_strong]:font-semibold",
  // A paragraph of screenshots is a grid: side by side where they fit, one per row on a phone.
  "[&_p:has(>a>img)]:grid [&_p:has(>a>img)]:grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] [&_p:has(>a>img)]:gap-3",
  "[&_img]:w-full [&_img]:rounded-md [&_img]:border [&_img]:border-border",
  "[&_hr]:my-6 [&_hr]:border-border",
  "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground [&_blockquote]:my-3",
  // inline code + fenced blocks
  "[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs [&_code]:font-mono",
  "[&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:border [&_pre]:border-border [&_pre]:bg-muted [&_pre]:p-3",
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-xs",
  // tables
  "[&_table]:my-3 [&_table]:w-full [&_table]:border-collapse [&_table]:text-sm",
  "[&_th]:border [&_th]:border-border [&_th]:bg-muted [&_th]:px-2.5 [&_th]:py-1.5 [&_th]:text-left",
  "[&_td]:border [&_td]:border-border [&_td]:px-2.5 [&_td]:py-1.5 [&_td]:align-top",
].join(" ");

export function MarkdownPage({ source }: { source: string }) {
  return (
    <div className={PROSE}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSlug]} components={{
        // Each image opens full size in a new tab.
        img: ({ src, alt }) => <a href={src} target="_blank" rel="noreferrer"><img src={src} alt={alt} /></a>,
      }}>
        {source}
      </ReactMarkdown>
    </div>
  );
}
