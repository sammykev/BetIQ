"use client";

import { useEffect, useState } from "react";
import { X, Megaphone } from "lucide-react";

export function AnnouncementBanner({ text }: { text: string }) {
  const [dismissed, setDismissed] = useState(false);

  // Persist dismissed state in sessionStorage — resets on new tab/session
  // but survives navigation within the same session
  useEffect(() => {
    if (!text) return;
    const key = `betiq-banner-${btoa(text).slice(0, 16)}`;
    if (sessionStorage.getItem(key)) setDismissed(true);
  }, [text]);

  const dismiss = () => {
    if (!text) return;
    const key = `betiq-banner-${btoa(text).slice(0, 16)}`;
    sessionStorage.setItem(key, "1");
    setDismissed(true);
  };

  if (!text || dismissed) return null;

  return (
    <div className="relative z-40 bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900 text-sm px-4 py-2.5 flex items-center gap-3">
      <Megaphone size={14} className="shrink-0 text-brand-400 dark:text-brand-600" />
      <p className="flex-1 text-center font-medium">{text}</p>
      <button onClick={dismiss} className="shrink-0 hover:opacity-60 transition-opacity p-1">
        <X size={14} />
      </button>
    </div>
  );
}
