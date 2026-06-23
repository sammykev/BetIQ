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
    <div className="sticky top-0 z-20 bg-blue-600 text-white text-sm px-4 py-2.5 flex items-center gap-3 shadow-md">
      <Megaphone size={14} className="shrink-0" />
      <p className="flex-1 text-center font-medium">{text}</p>
      <button onClick={dismiss} className="shrink-0 hover:opacity-70 transition-opacity p-1">
        <X size={14} />
      </button>
    </div>
  );
}
