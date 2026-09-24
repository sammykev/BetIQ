"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { useUser } from "@clerk/nextjs";

// Page views for the admin panel's traffic view (backend/traffic.py), sent
// to our own /api/t route, which adds the visitor's location. A visitor is a
// random id kept in this browser — no names, emails or IP addresses. Skipped
// for Do Not Track and on the admin pages.

const VISITOR_KEY = "betiq:visitor";
const SESSION_KEY = "betiq:session";
const SESSION_MINUTES = 30;

function visitorId(): { id: string; isNew: boolean } {
  try {
    const known = localStorage.getItem(VISITOR_KEY);
    if (known) return { id: known, isNew: false };
    const id = crypto.randomUUID();
    localStorage.setItem(VISITOR_KEY, id);
    return { id, isNew: true };
  } catch {
    return { id: "no-storage", isNew: false };
  }
}

/** True when this view starts a new visit (30 minutes without a page view). */
function startsSession(): boolean {
  try {
    const last = Number(sessionStorage.getItem(SESSION_KEY) || 0);
    sessionStorage.setItem(SESSION_KEY, String(Date.now()));
    return !last || Date.now() - last > SESSION_MINUTES * 60_000;
  } catch {
    return true;
  }
}

export function TrafficBeacon() {
  const pathname = usePathname();
  const { user, isLoaded } = useUser();
  const first = useRef(true);

  useEffect(() => {
    if (!isLoaded || !pathname || pathname.startsWith("/betiq-hq")) return;
    if (navigator.doNotTrack === "1") return;
    const { id, isNew } = visitorId();
    const params = new URLSearchParams(window.location.search);
    const meta = user?.publicMetadata as { subscription?: string; subscription_expires?: string } | undefined;
    const premium = meta?.subscription === "premium" && new Date(meta?.subscription_expires ?? 0) > new Date();
    let referrer = "";
    if (first.current && document.referrer) {
      try {
        const host = new URL(document.referrer).hostname;
        if (host !== window.location.hostname) referrer = host;
      } catch { /* ignore */ }
    }
    const body = JSON.stringify({
      path: pathname, visitor: id, new_visitor: isNew, new_session: startsSession(),
      referrer, lang: navigator.language,
      plan: !user ? "anon" : premium ? "premium" : "free",
      utm_source: params.get("utm_source"), utm_medium: params.get("utm_medium"),
      utm_campaign: params.get("utm_campaign"),
    });
    first.current = false;
    try {
      if (!navigator.sendBeacon?.("/api/t", new Blob([body], { type: "application/json" })))
        fetch("/api/t", { method: "POST", body, headers: { "Content-Type": "application/json" }, keepalive: true }).catch(() => {});
    } catch { /* never break the page for analytics */ }
  }, [pathname, isLoaded, user]);

  return null;
}
