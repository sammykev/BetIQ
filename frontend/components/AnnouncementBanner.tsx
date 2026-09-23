"use client";

import { useEffect, useState } from "react";
import { X, Megaphone } from "lucide-react";

/** Storage key for a banner's dismissed state. A string hash, not btoa():
 *  btoa throws on anything outside Latin-1 (₦, emoji, "—"), which crashed
 *  the whole page for such banners. */
function bannerKey(text: string): string {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (Math.imul(h, 31) + text.charCodeAt(i)) | 0;
  return `betiq-banner-${(h >>> 0).toString(36)}`;
}

export function AnnouncementBanner({ text }: { text: string }) {
  const [dismissed, setDismissed] = useState(false);

  // Persist dismissed state in sessionStorage — resets on new tab/session
  // but survives navigation within the same session
  useEffect(() => {
    if (!text) return;
    try {
      if (sessionStorage.getItem(bannerKey(text))) setDismissed(true);
    } catch { /* storage unavailable (e.g. private mode) — just show it */ }
  }, [text]);

  const dismiss = () => {
    if (!text) return;
    try { sessionStorage.setItem(bannerKey(text), "1"); } catch { /* ignore */ }
    setDismissed(true);
  };

  if (!text || dismissed) return null;

  return (
    <div className="relative z-40 bg-brand-400 text-ink text-sm px-4 py-2 flex items-center gap-3">
      <Megaphone size={14} className="shrink-0" />
      <p className="flex-1 text-center font-semibold">{text}</p>
      <button onClick={dismiss} aria-label="Dismiss announcement" className="shrink-0 hover:opacity-60 transition-opacity p-1">
        <X size={14} />
      </button>
    </div>
  );
}
