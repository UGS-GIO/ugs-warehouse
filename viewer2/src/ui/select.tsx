/**
 * Token-themed Base UI Select — keyboard, typeahead and screen-reader behaviour the native control
 * only half-gives us across platforms, with consistent styling and bigger touch targets. Generic
 * over the string value so callers keep their literal-union types with no cast.
 */
import { Select } from "@base-ui/react/select";

export interface SelectItem<T extends string> {
  value: T;
  label: string;
}

export function UiSelect<T extends string>({ value, onValueChange, items, className = "", title, disabled }: {
  value: T;
  onValueChange: (value: T) => void;
  items: readonly SelectItem<T>[];
  className?: string;
  title?: string;
  disabled?: boolean;
}) {
  return (
    <Select.Root
      value={value}
      // Base UI can emit null (a clearable select); ours always have a value, so ignore it.
      onValueChange={(v) => { if (v != null) onValueChange(v); }}
      items={items}
      disabled={disabled}
    >
      <Select.Trigger
        title={title}
        className={`flex items-center justify-between gap-1.5 rounded-md border border-input bg-card px-2 py-1 text-foreground hover:border-ring focus:border-ring focus:outline-none disabled:opacity-60 ${className}`}
      >
        <Select.Value className="truncate" />
        <Select.Icon className="shrink-0 text-muted-foreground">▾</Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Positioner sideOffset={4} className="z-50">
          <Select.Popup className="max-h-72 overflow-y-auto rounded-md border border-border bg-card py-1 text-sm text-foreground shadow-lg">
            {items.map((it) => (
              <Select.Item
                key={it.value}
                value={it.value}
                className="flex cursor-pointer select-none items-center justify-between gap-3 px-3 py-1.5 outline-none data-[highlighted]:bg-muted data-[selected]:font-medium"
              >
                <Select.ItemText>{it.label}</Select.ItemText>
                <Select.ItemIndicator className="text-primary">✓</Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Popup>
        </Select.Positioner>
      </Select.Portal>
    </Select.Root>
  );
}
