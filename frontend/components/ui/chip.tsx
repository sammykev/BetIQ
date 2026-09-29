"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

// A filter toggle: the re-skinned .chip classes, with the pressed state announced.
export const Chip = React.forwardRef<HTMLButtonElement, React.ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }>(
  ({ className, active, type = "button", ...props }, ref) => (
    <button ref={ref} type={type} aria-pressed={active} className={cn("chip", active ? "chip-active" : "chip-idle", className)} {...props} />
  )
);
Chip.displayName = "Chip";
