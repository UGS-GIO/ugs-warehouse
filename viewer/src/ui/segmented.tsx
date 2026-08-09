/**
 * Token-themed single-select segmented control on Base UI ToggleGroup — toolbar/radio semantics and
 * arrow-key navigation, which a row of hand-rolled buttons has neither of. Always keeps one option
 * selected (deselecting the active one is ignored). Generic over the string value so callers keep
 * their literal-union types with no cast.
 */
import { Toggle } from "@base-ui/react/toggle";
import { ToggleGroup } from "@base-ui/react/toggle-group";

export function UiSegmented<T extends string>({ value, onValueChange, items, className = "" }: {
  value: T;
  onValueChange: (value: T) => void;
  items: readonly { value: T; label: string }[];
  className?: string;
}) {
  return (
    <ToggleGroup
      value={[value]}
      onValueChange={(next) => {
        const v = next[0];
        if (v != null) onValueChange(v);   // ignore deselect — one is always active
      }}
      className={`flex overflow-hidden rounded-md border border-input text-xs ${className}`}
    >
      {items.map((it) => (
        <Toggle
          key={it.value}
          value={it.value}
          className="cursor-pointer select-none px-2 py-1 text-foreground hover:bg-muted data-[pressed]:bg-primary data-[pressed]:text-primary-foreground"
        >
          {it.label}
        </Toggle>
      ))}
    </ToggleGroup>
  );
}
