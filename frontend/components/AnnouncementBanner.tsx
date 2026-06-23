"use client";

import { useState } from "react";
import { X, Megaphone } from "lucide-react";

export function AnnouncementBanner({ text }: { text: string }) {
  const [dismissed, setDismissed] = useState(false);
  if (!text || dismissed) return null;
  return (
    <div className="bg-blue-600 text-white text-sm px-4 py-2.5 flex items-center gap-3">
      <Megaphone size={14} className="shrink-0" />
      <p className="flex-1 text-center font-medium">{text}</p>
      <button onClick={() => setDismissed(true)} className="shrink-0 hover:opacity-70 transition-opacity">
        <X size={14} />
      </button>
    </div>
  );
}
