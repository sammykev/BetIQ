import clsx from "clsx";
import { AlertTriangle } from "lucide-react";

// A club in this match has fewer than 5 matches in the model's data, or the
// match is in a league whose walk-forward check hasn't passed yet (league):
// the optimizer and daily slips leave the match out.

const WHY = "One of these clubs has fewer than 5 matches in our data, so this prediction is mostly guesswork. "
  + "It's left out of the optimizer and daily slips.";
const WHY_LEAGUE = "We've only just started predicting this league and our record here isn't proven yet. "
  + "It's left out of the optimizer and daily slips until it is.";

export function ThinHistoryBadge({ className, long = false, league = false }:
  { className?: string; long?: boolean; league?: boolean }) {
  const why = league ? WHY_LEAGUE : WHY;
  const text = league
    ? (long ? "New league for us: our record here isn't proven yet" : "New league")
    : (long ? "Little data on these clubs: treat this prediction with care" : "Little data");
  return (
    <span title={why} className={clsx(
      "inline-flex items-center gap-1.5 rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-[11px] font-semibold text-warn",
      className)}>
      <AlertTriangle size={12} className="shrink-0" aria-hidden="true" />
      {text}
      <span className="sr-only">. {why}</span>
    </span>
  );
}
