import { useEffect, useState } from "react";

// Logged-in-user badge for the internal/review deploy. The IAP serving app exposes /whoami from the
// X-Goog-Authenticated-User-Email header; the public CDN deploy has no such route (404), so this
// renders nothing there. Display-only — IAP already gated access.
export function UserBadge() {
  const [user, setUser] = useState<{ email: string; user: string } | null>(null);
  useEffect(() => {
    let live = true;
    fetch("/whoami", { headers: { accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (live && d?.email) setUser(d); })
      .catch(() => {});
    return () => { live = false; };
  }, []);
  if (!user) return null;
  return (
    <span
      title={`Signed in as ${user.email}`}
      className="ml-1 flex items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-card px-2 py-0.5 text-[11px] text-muted-foreground">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-green-500" aria-hidden />
      {user.user}
    </span>
  );
}
