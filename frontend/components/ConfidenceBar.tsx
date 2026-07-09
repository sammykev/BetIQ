"use client";

interface Props {
  value: number; // 0–1
  label?: string;
  color?: "green" | "yellow" | "red" | "blue";
}

const colorMap = {
  green: "bg-brand-500",
  yellow: "bg-amber-400",
  red: "bg-rose-500",
  blue: "bg-sky-500",
};

export function ConfidenceBar({ value, label, color = "green" }: Props) {
  const pct = Math.round(value * 100);
  const barColor =
    color === "green"
      ? value >= 0.75
        ? "bg-brand-500"
        : value >= 0.6
        ? "bg-amber-400"
        : "bg-orange-400"
      : colorMap[color];

  return (
    <div className="space-y-1">
      {label && (
        <div className="flex justify-between text-xs">
          <span className="text-zinc-400 dark:text-zinc-500">{label}</span>
          <span className="tnum text-zinc-800 dark:text-zinc-200 font-medium">{pct}%</span>
        </div>
      )}
      <div className="h-1.5 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
