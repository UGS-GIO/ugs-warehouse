// UGRC's keyless Utah locator, the one ugs-map-viewer uses: GNIS place names, cities, addresses.
const LOCATOR = "https://masquerade.ugrc.utah.gov/arcgis/rest/services/UtahLocator/GeocodeServer";

export type Suggestion = { text: string; magicKey: string };
export type Bounds = [number, number, number, number];

const wait = (ms: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  const t = setTimeout(resolve, ms);
  signal.addEventListener("abort", () => { clearTimeout(t); reject(signal.reason); });
});

export async function suggest(text: string, signal: AbortSignal): Promise<Suggestion[]> {
  await wait(250, signal); // the debounce: a newer keystroke changes the query key and aborts this
  const q = new URLSearchParams({ text, maxSuggestions: "8", f: "json" });
  const r = await fetch(`${LOCATOR}/suggest?${q}`, { signal });
  if (!r.ok) throw new Error(`suggest ${r.status}`);
  return (await r.json()).suggestions ?? [];
}

export async function locate(s: Suggestion): Promise<Bounds> {
  const q = new URLSearchParams({ SingleLine: s.text, magicKey: s.magicKey, outSR: '{"wkid":4326}', f: "json" });
  const r = await fetch(`${LOCATOR}/findAddressCandidates?${q}`);
  if (!r.ok) throw new Error(`locate ${r.status}`);
  const c = (await r.json()).candidates?.[0];
  if (!c) throw new Error("not found");
  const e = c.extent;
  return e ? [e.xmin, e.ymin, e.xmax, e.ymax] : [c.location.x, c.location.y, c.location.x, c.location.y];
}
