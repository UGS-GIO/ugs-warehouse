/**
 * Token-themed Base UI Slider, for one value or a [lo, hi] range (two thumbs). Keyboard, ARIA, and
 * a bigger touch target than a native `<input type="range">`.
 */
import { Slider } from "@base-ui/react/slider";

export function UiSlider<T extends number | [number, number]>({ value, onValueChange, onValueCommitted, min, max,
  step = 1, className = "", label, labels, valueText }: {
  value: T;
  onValueChange: (value: T) => void;
  onValueCommitted?: (value: T, reason: string) => void;
  min: number;
  max: number;
  step?: number;
  className?: string;
  label?: string;                            // one thumb's accessible name
  labels?: [string, string];                 // a range's two thumbs'
  valueText?: (value: number) => string;     // what a screen reader announces for a thumb
}) {
  const range = Array.isArray(value);
  const out = (v: number | readonly number[]) => (range ? [...(v as number[])] : Array.isArray(v) ? v[0] : v) as T;
  return (
    <Slider.Root
      value={value}
      onValueChange={(v) => onValueChange(out(v))}
      onValueCommitted={onValueCommitted && ((v, d) => onValueCommitted(out(v), d.reason))}
      min={min}
      max={max}
      step={step}
      thumbCollisionBehavior="none"
      className={`w-full ${className}`}
    >
      <Slider.Control className="flex h-6 w-full touch-none select-none items-center">
        <Slider.Track className="relative h-1.5 w-full rounded-full bg-muted">
          <Slider.Indicator className="h-full rounded-full bg-primary" />
          {(range ? [0, 1] : [0]).map((i) => (
            <Slider.Thumb key={i} index={range ? i : undefined} aria-label={range ? labels?.[i] : label}
              getAriaValueText={valueText ? (_f, v) => valueText(v) : undefined}
              className="size-4 rounded-full border-2 border-card bg-primary shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          ))}
        </Slider.Track>
      </Slider.Control>
    </Slider.Root>
  );
}
