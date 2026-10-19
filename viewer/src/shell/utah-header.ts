import { type ActionItem, loadHeader, setUtahHeaderSettings, type SettingsInput } from "@utahdts/utah-design-system-header";
import { useEffect } from "react";
import utahLogo from "@/assets/utah-logo.png";
import { IS_REVIEW } from "@/stac";

// The IAP user as a Utah-header action item (top-right of the official banner). Display-only — IAP
// already gated access; the username shows, the full email is the tooltip. Clicking signs out via
// IAP's clear-login-cookie flow. The icon is an HTML string, not an element: the header renders each
// item twice (desktop bar and mobile menu), and one element would be moved into the second.
function userActionItem(email: string): ActionItem {
  return {
    title: email.split("@")[0],
    showTitle: true,
    icon: `<span title="Signed in as ${email.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)}">👤</span>`,
    className: "ugs-user-badge",
    // click → IAP sign-out (clears the login cookie, forces re-auth).
    actionFunction: () => { window.location.href = "/?gcp-iap-mode=CLEAR_LOGIN_COOKIE"; },
  };
}

// Official State of Utah header — injects the state identity bar + maintained logo above the
// app (Utah Design System standard). On the review deploy the IAP user shows as a top-right
// action item (same level as the UGS title/logo), fetched from /whoami. Configured on mount.
export function useUtahHeader() {
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const base: SettingsInput = {
        title: "Utah Geological Survey",
        showTitle: true,
        titleUrl: "https://geology.utah.gov",
        // `htmlString` not `imageUrl`: the latter emits an <img> with no alt. Decorative here.
        logo: { htmlString: `<img src="${utahLogo}" alt="" />` },
        mainMenu: false,
        utahId: false,
        size: "SMALL",   // MEDIUM (the default) took about 100 px above every view
      };
      let email = "";
      if (IS_REVIEW) {
        try {
          const r = await fetch("/whoami", { headers: { accept: "application/json" } });
          if (r.ok) email = (await r.json())?.email ?? "";
        } catch { /* public deploy / local: no /whoami */ }
        // Local dev preview: no IAP here, so surface a placeholder to see the badge.
        if (!email && import.meta.env.DEV) email = "dev.user@utah.gov";
      }
      if (cancelled) return;
      setUtahHeaderSettings(email ? { ...base, actionItems: [userActionItem(email)] } : base);
      loadHeader();
    })();
    return () => { cancelled = true; };
  }, []);
}
