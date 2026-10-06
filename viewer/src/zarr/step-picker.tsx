/**
 * What a datacube shows: a variable select, and per non-spatial dim ‹ › to step through it plus one
 * select per calendar field the series resolves to (Year, Month, Day). Undated dims list labels.
 */
import { type CubeStep, MONTH_NAMES, stepKey } from "@/stac";
import { UiSelect } from "@/ui/select";
import { type CubePicks, VAR } from "./cube-picks";
import { type Field, FIELDS, stepFor } from "./steps";

const SELECT_CLASS = "text-xs pointer-coarse:min-h-11 pointer-coarse:text-sm";
const ARROW_CLASS = "flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-input bg-card text-foreground "
  + "hover:border-ring disabled:opacity-40 pointer-coarse:h-11 pointer-coarse:w-11";

/** The variable select and the step pickers, writing picks by key (see cube-picks). */
export function CubeControls({ variables, variable, stepDims, selection, onPick }: {
  variables: string[]; variable: string;
  stepDims: Record<string, CubeStep[]>;
  selection: Record<string, number>;
  onPick: (key: keyof CubePicks, value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      {variables.length > 1 && (
        <UiSelect className={`${SELECT_CLASS} max-w-full`} title="Variable" value={variable}
          items={variables.map((v) => ({ value: v, label: v.replaceAll("_", " ") }))}
          onValueChange={(v) => onPick(VAR, v)} />
      )}
      <StepPickers stepDims={stepDims} selection={selection}
        onChange={(d, i) => onPick(d, stepKey(stepDims[d][i], i))} />
    </div>
  );
}

export function StepPickers({ stepDims, selection, onChange }: {
  stepDims: Record<string, CubeStep[]>;
  selection: Record<string, number>;
  onChange: (dim: string, index: number) => void;
}) {
  const dims = Object.entries(stepDims).filter(([, steps]) => steps.length > 1);
  if (!dims.length) return null;
  return (
    <div className="flex flex-col gap-1.5">
      {dims.map(([dim, steps]) => (
        <StepPicker key={dim} dim={dim} steps={steps} index={selection[dim] ?? 0}
          onChange={(i) => onChange(dim, i)} />
      ))}
    </div>
  );
}

const FIELD_LABEL: Record<Field, string> = { year: "Year", month: "Month", day: "Day" };
const fieldLabel = (f: Field, v: number) => (f === "month" ? MONTH_NAMES[v - 1] : String(v));

function StepPicker({ dim, steps, index, onChange }: {
  dim: string; steps: CubeStep[]; index: number; onChange: (index: number) => void;
}) {
  const cur = steps[index] ?? steps[0];
  const fields = FIELDS.filter((f) => steps.every((s) => s[f] !== undefined));
  const label = dim[0].toUpperCase() + dim.slice(1);

  const selects = fields.length
    ? fields.map((f) => {
      const at = FIELDS.indexOf(f);
      // Only values under the current coarser fields: no picking a month the year lacks.
      const values = [...new Set(steps
        .filter((s) => FIELDS.slice(0, at).every((c) => s[c] === cur[c]))
        .map((s) => s[f]!))];
      return (
        <UiSelect key={f} className={`${SELECT_CLASS} ${f === "year" ? "w-20" : "w-16"}`} title={FIELD_LABEL[f]}
          value={String(cur[f])} items={values.map((v) => ({ value: String(v), label: fieldLabel(f, v) }))}
          onValueChange={(v) => onChange(stepFor(steps, cur, f, Number(v)))} />
      );
    })
    : [<UiSelect key="step" className={SELECT_CLASS} title={label} value={String(index)}
        items={steps.map((s, i) => ({ value: String(i), label: s.label }))}
        onValueChange={(i) => onChange(Number(i))} />];

  return (
    <div className="flex items-center gap-1.5" role="group" aria-label={label}>
      <button type="button" className={ARROW_CLASS} disabled={index <= 0} onClick={() => onChange(index - 1)}
        aria-label={`Previous ${dim}`} title="Previous"><span aria-hidden>‹</span></button>
      {selects}
      <button type="button" className={ARROW_CLASS} disabled={index >= steps.length - 1}
        onClick={() => onChange(index + 1)} aria-label={`Next ${dim}`} title="Next"><span aria-hidden>›</span></button>
    </div>
  );
}
