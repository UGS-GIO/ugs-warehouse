// STAC item properties as a key/value table. Shared so the catalog detail view and the map
// detail panel render an item the same way — they had drifted copies, and the map one dumped
// whole `ugs:renders` blobs into a cell.

// Structural properties with their own UI elsewhere (the style, the legend, the contents list).
// Their raw JSON is long enough to bury everything else in the table.
const SKIP = new Set(["ugs:renders", "ugs:contents", "classification:classes"]);

/** Drop the `ugs:` prefix, underscores → spaces. */
const prettyKey = (k: string) => k.replace(/^ugs:/, "").replace(/_/g, " ");

/** Nothing renders as `[object Object]` — arrays of objects stringify per element, not via join. */
const fmtVal = (v: unknown): string =>
  Array.isArray(v) ? v.map(fmtVal).join(", ")
    : v && typeof v === "object" ? JSON.stringify(v)
      : String(v);

export function PropertyTable({ properties, className = "mt-3" }: {
  properties: Record<string, unknown>;
  className?: string;
}) {
  const rows = Object.entries(properties)
    .filter(([k, v]) => v !== null && v !== "" && !SKIP.has(k));
  if (!rows.length) return null;
  return (
    <table className={`w-full border-collapse text-sm sm:table-fixed ${className}`}>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td className="block break-words px-2.5 pt-1.5 align-top text-xs uppercase tracking-wide text-muted-foreground sm:table-cell sm:w-44 sm:border-b sm:border-border sm:py-1 sm:text-sm sm:normal-case sm:tracking-normal">{prettyKey(k)}</td>
            <td className="block break-words border-b border-border px-2.5 pb-1.5 align-top sm:table-cell sm:py-1">{fmtVal(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
