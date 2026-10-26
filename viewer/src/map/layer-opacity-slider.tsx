import { UiSlider } from "@/ui/slider";
import { useLayerOpacity } from "./layer-opacity";

/** An active layer's opacity, under its row in the layer list. */
export function LayerOpacitySlider({ id, title }: { id: string; title: string }) {
  const [opacity, setOpacity] = useLayerOpacity(id);
  return (
    <div className="flex items-center gap-2 py-1 text-xs text-muted-foreground">
      <span className="shrink-0">Opacity</span>
      <UiSlider value={Math.round(opacity * 100)} onValueChange={(v) => setOpacity(v / 100)}
        min={0} max={100} step={5} label={`Opacity of ${title}`} />
      <span className="w-9 shrink-0 text-right tabular-nums">{Math.round(opacity * 100)}%</span>
    </div>
  );
}
