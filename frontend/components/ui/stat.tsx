import * as React from "react";
import { cn } from "@/lib/utils";

// A number that must read at a glance: scoreboard face, tabular figures.
export function Stat({ label, value, hint, className, tone = "default" }: {
  label: React.ReactNode;
  value: React.ReactNode;
  hint?: React.ReactNode;
  className?: string;
  tone?: "default" | "accent" | "danger";
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <p className="eyebrow">{label}</p>
      <p className={cn("display text-[28px] leading-none mt-1.5 tnum",
        tone === "accent" ? "text-accent" : tone === "danger" ? "text-danger" : "text-n-0")}>{value}</p>
      {hint && <p className="mt-1 text-[12px] text-n-400 text-pretty">{hint}</p>}
    </div>
  );
}
