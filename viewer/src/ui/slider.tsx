/**
 * Token-themed Base UI Slider — accessible single-value range (keyboard, ARIA, a bigger touch
 * target than a native `<input type="range">`).
 */
import { Slider } from "@base-ui/react/slider";

export function UiSlider({ value, onValueChange, min, max, step, className = "", label }: {
  value: number;
  onValueChange: (value: number) => void;
  min: number;
  max: number;
  step: number;
  className?: string;
  label?: string;
}) {
  return (
    <Slider.Root
      value={value}
      onValueChange={(v) => onValueChange(Array.isArray(v) ? v[0] : v)}
      min={min}
      max={max}
      step={step}
      className={`w-full ${className}`}
    >
      <Slider.Control className="flex h-6 w-full touch-none select-none items-center">
        <Slider.Track className="relative h-1.5 w-full rounded-full bg-muted">
          <Slider.Indicator className="h-full rounded-full bg-primary" />
          <Slider.Thumb aria-label={label}
            className="size-4 rounded-full border-2 border-card bg-primary shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
        </Slider.Track>
      </Slider.Control>
    </Slider.Root>
  );
}

/** Two-thumb range on the same Base UI Slider: drag updates `onValueChange`, release commits. */
export function UiRangeSlider({ value, onValueChange, onValueCommitted, min, max, step = 1, labels, valueText, className = "" }: {
  value: [number, number];
  onValueChange: (value: [number, number]) => void;
  onValueCommitted: (value: [number, number], reason: string) => void;
  min: number;
  max: number;
  step?: number;
  labels: [string, string];                  // each thumb's accessible name
  valueText?: (value: number) => string;     // what a screen reader announces for a thumb
  className?: string;
}) {
  const pair = (v: number | readonly number[]): [number, number] =>
    (Array.isArray(v) ? [v[0], v[1]] : [v as number, v as number]);
  return (
    <Slider.Root
      value={value}
      onValueChange={(v) => onValueChange(pair(v))}
      onValueCommitted={(v, d) => onValueCommitted(pair(v), d.reason)}
      min={min}
      max={max}
      step={step}
      thumbCollisionBehavior="none"
      className={`w-full ${className}`}
    >
      <Slider.Control className="flex h-6 w-full touch-none select-none items-center">
        <Slider.Track className="relative h-1.5 w-full rounded-full bg-muted">
          <Slider.Indicator className="h-full rounded-full bg-primary" />
          {[0, 1].map((i) => (
            <Slider.Thumb key={i} index={i} aria-label={labels[i]}
              getAriaValueText={valueText ? (_f, v) => valueText(v) : undefined}
              className="size-4 rounded-full border-2 border-card bg-primary shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          ))}
        </Slider.Track>
      </Slider.Control>
    </Slider.Root>
  );
}
