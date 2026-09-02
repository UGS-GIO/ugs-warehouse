// The dataset's column schema (name + type + description) from the STAC Table extension
// (`table:columns` on the GeoParquet `data` asset). Empty-safe: a dataset with no published schema
// (many pubs, compact index rows) shows a note instead of a bare table. Ported from the reference
// SchemaTable onto the viewer's UDS tokens; the description column shows only when any row has one.
import type { TableColumn } from "./stac";

export function SchemaTable({ columns }: { columns: TableColumn[] | undefined }) {
  if (!columns || columns.length === 0) {
    return <p role="note" className="text-sm text-muted-foreground">No column schema is published for this dataset.</p>;
  }
  const hasDesc = columns.some((c) => c.description);
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/50 text-left">
            <th className="px-3 py-2 font-semibold text-foreground">Column</th>
            <th className="px-3 py-2 font-semibold text-foreground">Type</th>
            {hasDesc && <th className="px-3 py-2 font-semibold text-foreground">Description</th>}
          </tr>
        </thead>
        <tbody>
          {columns.map((c, i) => (
            <tr key={`${c.name}-${i}`} className="border-b border-border last:border-0">
              <td className="px-3 py-1.5"><code className="font-mono text-xs text-foreground">{c.name}</code></td>
              <td className="px-3 py-1.5 text-muted-foreground">{c.type}</td>
              {hasDesc && <td className="px-3 py-1.5 text-muted-foreground">{c.description}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
