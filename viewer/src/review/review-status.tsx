import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ItemStatus, ITEM_STATUSES, listItemStatuses, setItemStatus } from "./comments";
import { UiSelect } from "../ui/select";

// Per-layer review status — set on a layer's Review box, summarized on the dashboard. Independent of
// comment resolution: a reviewer marks the layer's overall progress (…→ approved = ready to promote).

const LABEL: Record<string, string> = {
  pending: "Pending", in_review: "In review", changes_requested: "Changes requested", approved: "Approved",
};
export const statusLabel = (s: string) => LABEL[s] ?? s;

// Tailwind classes per status (badge + select tint). Neutral → amber → green arc.
export const statusClass = (s: string): string => ({
  pending: "border-border bg-muted text-muted-foreground",
  in_review: "border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-400",
  changes_requested: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  approved: "border-green-500/40 bg-green-500/10 text-green-600 dark:text-green-400",
}[s] ?? "border-border bg-muted text-muted-foreground");

// Shared query for all layer statuses (dashboard summary + each item's current value read from it).
export function useItemStatuses() {
  return useQuery({ queryKey: ["item-status"], queryFn: listItemStatuses, retry: false });
}

// A labelled dropdown that sets one layer's review status.
export function LayerStatusControl({ itemId }: { itemId: string }) {
  const qc = useQueryClient();
  const { data: all = [] } = useItemStatuses();
  const current = all.find((s: ItemStatus) => s.item_id === itemId)?.status ?? "pending";
  const set = useMutation({
    mutationFn: (status: string) => setItemStatus(itemId, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["item-status"] }),
  });
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="font-medium">Layer status</span>
      <UiSelect value={current} disabled={set.isPending} onValueChange={(v) => set.mutate(v)}
        items={ITEM_STATUSES.map((s) => ({ value: s, label: statusLabel(s) }))}
        className={`px-1.5 py-0.5 ${statusClass(current)}`} />
      {set.isPending && <span className="text-muted-foreground">saving…</span>}
    </label>
  );
}
