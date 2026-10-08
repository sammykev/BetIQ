import clsx from "clsx";
import { AlertTriangle } from "lucide-react";

// A club in this match has fewer than 5 matches in the model's data: its
// chances lean on the bookmaker's prices more than on our own record, and
// the optimizer and daily slips leave the match out.

const WHY = "One of these clubs has fewer than 5 matches in our data, so this prediction is mostly guesswork. "
  + "It's left out of the optimizer and daily slips.";

export function ThinHistoryBadge({ className, long = false }: { className?: string; long?: boolean }) {
  return (
    <span title={WHY} className={clsx(
      "inline-flex items-center gap-1.5 rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-[11px] font-semibold text-warn",
      className)}>
      <AlertTriangle size={12} className="shrink-0" aria-hidden="true" />
      {long ? "Little data on these clubs: treat this prediction with care" : "Little data"}
      <span className="sr-only">. {WHY}</span>
    </span>
  );
}
