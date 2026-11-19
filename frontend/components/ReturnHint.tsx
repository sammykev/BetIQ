"use client";

import clsx from "clsx";

/** What ₦100 on a slip returns on average: its chance × its odds, with
 *  SportyBet's sure multi-bet bonus (backend multi_bonus.returns). */
export function ReturnHint({ expected, bonus, bonusTop }: {
  expected?: number | null; bonus?: number | null; bonusTop?: number | null;
}) {
  if (typeof expected !== "number") return null;
  const worth = expected >= 1;
  const back = Math.round(expected * 100);
  return (
    <div className={clsx("rounded-xl px-3 py-2.5 text-sm", worth ? "bg-accent/10 text-n-100" : "bg-surface-sunken text-n-300")}>
      <p>
        ₦100 on this slip returns about{" "}
        <span className={clsx("font-bold tnum", worth ? "text-accent" : "text-n-0")}>₦{back}</span> on average
        {bonus ? <> (with SportyBet&apos;s sure {+(bonus * 100).toFixed(1)}% multi-bet bonus)</> : null}.
      </p>
      <p className="text-[11px] text-n-400 mt-0.5 text-pretty">
        {worth
          ? "Worth more than it costs by our checked chances. It can still lose: that's the average over many slips."
          : `About ₦${100 - back} per ₦100 goes to SportyBet's cut on every leg.`}
        {(bonusTop ?? 0) > (bonus ?? 0) ? ` Its bonus could go up to ${+((bonusTop ?? 0) * 100).toFixed(1)}%; only the sure part is counted.` : ""}
      </p>
    </div>
  );
}
