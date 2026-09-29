import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// Small status tags. Colour is never alone: every badge has a word (and
// won/lost/live carry an icon or dot where they're used).
export const badgeVariants = cva(
  "inline-flex items-center gap-1 whitespace-nowrap rounded-full font-semibold text-[11px] leading-none px-2 h-5",
  {
    variants: {
      tone: {
        neutral: "bg-n-800/70 text-n-300",
        accent: "bg-brand-400/15 text-accent",
        solid: "bg-brand-400 text-ink",
        live: "bg-danger/12 text-danger",
        warn: "bg-warn/12 text-warn",
        danger: "bg-danger/12 text-danger",
        info: "bg-info/12 text-info",
      },
    },
    defaultVariants: { tone: "neutral" },
  }
);

export function Badge({ className, tone, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

/** A pulsing dot for "in play" (the one loop the app allows). */
export function LiveDot({ className }: { className?: string }) {
  return (
    <span className={cn("relative inline-flex w-1.5 h-1.5", className)} aria-hidden>
      <span className="absolute inset-0 rounded-full bg-danger opacity-60 animate-ping motion-reduce:animate-none" />
      <span className="relative inline-flex w-1.5 h-1.5 rounded-full bg-danger" />
    </span>
  );
}
