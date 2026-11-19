import clsx from "clsx";

/** Our chance as a bar, with a tick where SportyBet's price puts it. */
export function ChanceBar({ ours, implied, label = "Our chance" }: { ours: number; implied?: number | null; label?: string }) {
  const pct = (x: number) => `${Math.max(0, Math.min(100, x * 100))}%`;
  const beats = implied != null && ours >= implied;
  return (
    <span className="relative block h-1.5 w-full rounded-full bg-n-800/70 overflow-visible" role="img"
      aria-label={`${label} ${Math.round(ours * 100)}%${implied != null ? `, SportyBet's price ${Math.round(implied * 100)}%` : ""}`}>
      <span className={clsx("absolute inset-y-0 left-0 rounded-full", beats ? "bg-accent" : "bg-n-400")} style={{ width: pct(ours) }} />
      {implied != null && (
        <span aria-hidden className="absolute -top-[3px] h-3 w-[2px] rounded-full bg-n-0/70" style={{ left: `calc(${pct(implied)} - 1px)` }} />
      )}
    </span>
  );
}

/** What ₦100 returns on average, on a bar where the middle mark is ₦100. */
export function ReturnMeter({ value }: { value: number }) {
  const MAX = 2;                                   // ₦200 fills the bar
  const w = `${Math.min(100, (value / MAX) * 100)}%`;
  return (
    <span className="relative block h-2 w-full rounded-full bg-n-800/70" role="img"
      aria-label={`Returns about ₦${Math.round(value * 100)} per ₦100`}>
      <span className={clsx("absolute inset-y-0 left-0 rounded-full", value >= 1 ? "bg-accent" : "bg-n-500")} style={{ width: w }} />
      <span aria-hidden className="absolute -top-1 h-4 w-[2px] rounded-full bg-n-0/80" style={{ left: "calc(50% - 1px)" }} />
    </span>
  );
}

/** Home / draw / away chances as one split bar, the picked outcomes bright. */
export function OutcomeBar({ p, picked }: { p: number[]; picked: string }) {
  const keys = ["1", "X", "2"];
  const total = p.reduce((a, b) => a + b, 0) || 1;
  return (
    <span className="flex h-2 w-full gap-[2px] overflow-hidden rounded-full" role="img"
      aria-label={`Home ${Math.round(p[0] * 100)}%, draw ${Math.round(p[1] * 100)}%, away ${Math.round(p[2] * 100)}%`}>
      {keys.map((k, i) => (
        <span key={k} className={clsx("h-full", picked.includes(k) ? ["bg-accent", "bg-info", "bg-warn"][i] : "bg-n-800")}
          style={{ width: `${(p[i] / total) * 100}%` }} />
      ))}
    </span>
  );
}
