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
