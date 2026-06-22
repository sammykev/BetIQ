"use client";

interface Props {
  value: number; // 0–1
  label?: string;
  color?: "green" | "yellow" | "red" | "blue";
}

const colorMap = {
  green: "bg-green-500",
  yellow: "bg-yellow-400",
  red: "bg-red-500",
  blue: "bg-blue-500",
};

export function ConfidenceBar({ value, label, color = "green" }: Props) {
  const pct = Math.round(value * 100);
  const barColor =
    color === "green"
      ? value >= 0.75
        ? "bg-green-400"
        : value >= 0.6
        ? "bg-yellow-400"
        : "bg-orange-400"
      : colorMap[color];

  return (
    <div className="space-y-1">
      {label && (
        <div className="flex justify-between text-xs">
          <span className="text-slate-500 dark:text-slate-400">{label}</span>
          <span className="text-slate-800 dark:text-slate-200 font-medium">{pct}%</span>
        </div>
      )}
      <div className="h-1.5 bg-slate-300 dark:bg-slate-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
