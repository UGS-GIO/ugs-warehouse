// UGRC's keyless Utah locator, the one ugs-map-viewer uses: GNIS place names, cities, addresses.
const LOCATOR = "https://masquerade.ugrc.utah.gov/arcgis/rest/services/UtahLocator/GeocodeServer";

export type Suggestion = { text: string; magicKey: string };
export type Bounds = [number, number, number, number];

const wait = (ms: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  const onAbort = () => { clearTimeout(t); reject(signal.reason); };
  const t = setTimeout(() => { signal.removeEventListener("abort", onAbort); resolve(); }, ms);
  signal.addEventListener("abort", onAbort, { once: true });
});

// ArcGIS reports some failures as a 200 with an `error` body.
async function read(r: Response, what: string): Promise<Record<string, unknown>> {
  if (!r.ok) throw new Error(`${what} ${r.status}`);
  const body = await r.json();
  if (body?.error) throw new Error(`${what}: ${body.error.message ?? "service error"}`);
  return body;
}

export async function suggest(text: string, signal: AbortSignal): Promise<Suggestion[]> {
  await wait(250, signal); // the debounce: a newer keystroke changes the query key and aborts this
  const q = new URLSearchParams({ text, maxSuggestions: "8", f: "json" });
  const body = await read(await fetch(`${LOCATOR}/suggest?${q}`, { signal }), "suggest");
  if (!Array.isArray(body.suggestions)) throw new Error("suggest: no suggestions list");
  return body.suggestions;
}

export async function locate(s: Suggestion): Promise<Bounds> {
  const q = new URLSearchParams({ SingleLine: s.text, magicKey: s.magicKey, outSR: '{"wkid":4326}', f: "json" });
  const body = await read(await fetch(`${LOCATOR}/findAddressCandidates?${q}`), "locate");
  const c = (body.candidates as { extent?: Record<string, number>; location?: { x: number; y: number } }[] | undefined)?.[0];
  if (!c) throw new Error("not found");
  const e = c.extent;
  if (e) return [e.xmin, e.ymin, e.xmax, e.ymax];
  if (c.location) return [c.location.x, c.location.y, c.location.x, c.location.y];
  throw new Error("not found");
}
