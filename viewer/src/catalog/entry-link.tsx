import type { ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";

// A same-view link to another catalog entry (keeps the view and its other search keys). `terrain`
// is cleared because the 3D toggle belongs to the item it was set on; a newly opened one starts 2D.
export function EntryLink({ c, i, className, children }: { c: string; i: string; className?: string; children: ReactNode }) {
  return (
    <Link to="." search={(prev) => ({ ...prev, c, i, terrain: undefined })} className={className}>
      {children}
    </Link>
  );
}

// The same navigation, for a control that is not a link (a Jump to menu).
export function useOpenEntry(): (c: string, i: string) => void {
  const navigate = useNavigate();
  return (c, i) => navigate({ to: ".", search: (prev) => ({ ...prev, c, i, terrain: undefined }) });
}
