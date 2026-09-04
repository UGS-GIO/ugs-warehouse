import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { fullUrl, thumbUrl } from "./ucrc-photos";

// Thumbnail gallery + lightbox for a UCRC core-photo related table (enmin_ucrc_photos). URL helpers
// live in ./ucrc-photos (pure, unit-tested). The lightbox is hand-rolled (no shadcn Dialog / embla
// Carousel deps) — grid → click → full view + ‹/›.
const PAGE = 48;

type Row = Record<string, unknown>;
const captionOf = (r: Row) =>
  [r.photo_type, r.top_depth != null ? `${r.top_depth}′` : null].filter(Boolean).join(" · ");

export function PhotoGallery({ href }: { href: string }) {
  const [page, setPage] = useState(0);
  const [lightbox, setLightbox] = useState<number | null>(null);  // index into the current page's rows

  const { data, isLoading, error } = useQuery({
    queryKey: ["ucrc-photo-gallery", href, page],
    queryFn: async () => {
      const { queryParquet } = await import("@/data/download");
      // depth-order so a box's photos read top→bottom; range-read, never downloads the file.
      return queryParquet(href, { limit: PAGE, offset: page * PAGE, orderBy: "top_depth", desc: false });
    },
    retry: false,
  });

  const rows = ((data?.rows ?? []) as Row[]).filter((r) => r.storage_path);
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE));
  const btn = "rounded border border-border bg-card px-2 py-0.5 text-xs text-foreground hover:border-primary disabled:opacity-40";

  return (
    <div className="mt-2">
      {isLoading && <p className="text-xs text-muted-foreground">Loading photos…</p>}
      {error && <p className="text-xs text-muted-foreground">Couldn't load photos ({String(error)}).</p>}
      {!isLoading && !error && (
        <>
          <div className="mb-1.5 text-xs text-muted-foreground">{total.toLocaleString()} photos</div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
            {rows.map((r, i) => {
              const sp = String(r.storage_path);
              const cap = captionOf(r);
              return (
                <button key={i} onClick={() => setLightbox(i)}
                  className="group block overflow-hidden rounded border border-border bg-muted text-left"
                  title={String(r.filename ?? sp.split("/").pop())}>
                  <img src={thumbUrl(sp)} loading="lazy" alt={String(r.filename ?? "core photo")}
                    className="aspect-[4/3] w-full object-cover transition group-hover:opacity-90" />
                  {cap && <div className="truncate px-1 py-0.5 text-xs text-muted-foreground">{cap}</div>}
                </button>
              );
            })}
          </div>
          {pageCount > 1 && (
            <div className="mt-2 flex items-center gap-1.5 text-xs">
              <button className={btn} disabled={page === 0} onClick={() => setPage(0)}>«</button>
              <button className={btn} disabled={page === 0} onClick={() => setPage((p) => p - 1)}>‹ Prev</button>
              <span className="px-1 text-muted-foreground">Page {page + 1} of {pageCount}</span>
              <button className={btn} disabled={page + 1 >= pageCount} onClick={() => setPage((p) => p + 1)}>Next ›</button>
              <button className={btn} disabled={page + 1 >= pageCount} onClick={() => setPage(pageCount - 1)}>»</button>
            </div>
          )}
        </>
      )}

      {lightbox !== null && rows[lightbox] && (
        <Lightbox rows={rows} index={lightbox} onIndex={setLightbox} onClose={() => setLightbox(null)} />
      )}
    </div>
  );
}

// Full-screen overlay: the image + ‹/› nav + caption. Esc/click-backdrop closes, ←/→ navigate.
function Lightbox({ rows, index, onIndex, onClose }: {
  rows: Row[]; index: number; onIndex: (i: number) => void; onClose: () => void;
}) {
  const go = (d: number) => onIndex((index + d + rows.length) % rows.length);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, rows.length]);

  const r = rows[index];
  const sp = String(r.storage_path);
  const cap = [r.filename, captionOf(r)].filter(Boolean).join("  ·  ");
  const nav = "absolute top-1/2 -translate-y-1/2 rounded-full bg-black/50 px-3 py-2 text-2xl text-white hover:bg-black/70";

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 p-4" onClick={onClose}>
      <button className="absolute right-3 top-3 rounded-full bg-black/50 px-3 py-1 text-white hover:bg-black/70"
        onClick={onClose}>✕</button>
      {rows.length > 1 && <button className={`${nav} left-3`} onClick={(e) => { e.stopPropagation(); go(-1); }}>‹</button>}
      {rows.length > 1 && <button className={`${nav} right-3`} onClick={(e) => { e.stopPropagation(); go(1); }}>›</button>}
      <img src={fullUrl(sp)} alt={String(r.filename ?? "core photo")}
        className="max-h-[85vh] max-w-[92vw] object-contain" onClick={(e) => e.stopPropagation()} />
      <div className="mt-2 max-w-[92vw] truncate text-center text-xs text-white/80">
        {cap} <span className="text-white/50">({index + 1} of {rows.length})</span>
      </div>
    </div>
  );
}
