"use client";

import Link from "next/link";
import clsx from "clsx";
import { ArrowRight } from "lucide-react";
import type { DaySummary } from "@/lib/matchday";

type Counts = Pick<DaySummary, "total" | "finished" | "live" | "tip">;

/** How the day's picks did: our tips, goals tips, the most likely result
 * (football), or the picks given in `items` (basketball). */
export function DaySummaryBar({ summary, label, items: given, link = true }: {
  summary: Counts & Partial<DaySummary>; label: string;
  items?: { name: string; v: [number, number] }[]; link?: boolean;
}) {
  const items = given ?? [
    { name: "Our tips", v: summary.tip },
    { name: "Goals tips", v: summary.goals ?? [0, 0] },
    { name: "Most likely result", v: summary.favourite ?? [0, 0] },
  ];
  const settled = summary.tip[0] + summary.tip[1];
  return (
    <div className="card px-4 py-3 flex flex-wrap items-center gap-x-6 gap-y-2">
      <div className="min-w-0">
        <p className="eyebrow">{label}</p>
        <p className="text-xs text-n-400 tnum">
          {summary.finished} of {summary.total} played{summary.live ? ` · ${summary.live} live` : ""}
        </p>
      </div>
      {items.map(({ name, v }) => {
        const n = v[0] + v[1];
        const rate = n ? v[0] / n : null;
        return (
          <div key={name} className="min-w-0">
            <p className="text-[11px] text-n-500">{name}</p>
            <p className="font-display font-extrabold text-xl leading-none tnum">
              <span className={clsx(rate === null ? "text-n-500" : rate >= 0.6 ? "text-accent" : rate >= 0.4 ? "text-warn" : "text-danger")}>
                {v[0]}/{n}
              </span>
              {rate !== null && <span className="text-xs font-sans font-semibold text-n-500 ml-1.5">{Math.round(rate * 100)}%</span>}
            </p>
          </div>
        );
      })}
      {link && settled > 0 && (
        <Link href="/history" className="ml-auto text-xs font-semibold text-accent inline-flex items-center gap-1 hover:underline">
          Track record <ArrowRight size={12} />
        </Link>
      )}
    </div>
  );
}
