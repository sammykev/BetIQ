"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import clsx from "clsx";

/** "Why this pick": the server's short reasons (backend/reasons.py), folded away until asked for. */
export function WhyPick({ why, className }: { why?: string[] | null; className?: string }) {
  const [open, setOpen] = useState(false);
  if (!why?.length) return null;
  return (
    <div className={className}>
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        className="inline-flex items-center gap-0.5 text-[11px] font-semibold text-n-500 hover:text-n-200">
        Why this pick
        <ChevronDown size={11} className={clsx("transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ul className="mt-1 space-y-0.5 text-[11px] text-n-400 list-disc pl-4">
          {why.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}
    </div>
  );
}
