"use client";

import { getTeamAssets } from "@/lib/teamAssets";
import clsx from "clsx";

interface Props {
  home: string;
  away: string;
  date?: string;
  time?: string;
  league?: string;
  flag?: string;
  children?: React.ReactNode;      // extra content (tip badge, outcome, etc.)
  onClick?: () => void;
  className?: string;
}

function TeamName({ name }: { name: string }) {
  const { imageUrl, isFlag, color } = getTeamAssets(name);
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      {imageUrl ? (
        <span className="relative inline-flex items-center justify-center w-5 h-5 rounded-full bg-zinc-100 dark:bg-zinc-800 ring-1 ring-zinc-200/80 dark:ring-zinc-700 overflow-hidden shrink-0">
          <img
            src={imageUrl}
            alt={name}
            className={clsx("object-contain", isFlag ? "w-full h-full object-cover" : "w-[70%] h-[70%]")}
            onError={e => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
        </span>
      ) : (
        <span
          className="inline-flex items-center justify-center w-5 h-5 rounded-full text-white font-bold text-[8px] shrink-0"
          style={{ background: color }}
        >
          {name.slice(0, 2).toUpperCase()}
        </span>
      )}
      <span className="text-zinc-900 dark:text-white font-semibold text-sm truncate">{name}</span>
    </div>
  );
}

export function MatchCard({ home, away, date, time, league, flag, children, onClick, className }: Props) {
  return (
    <div
      onClick={onClick}
      className={clsx("card", onClick && "card-interactive", className)}
    >
      <div className="px-4 py-3 flex items-center gap-3">
        <div className="flex-1 min-w-0 space-y-1.5">
          {(league || flag) && (
            <p className="text-[10px] text-zinc-400 dark:text-zinc-500 font-semibold uppercase tracking-wide truncate">
              {flag} {league}{date ? ` · ${date}${time && time !== "TBD" ? ` ${time}` : ""}` : ""}
            </p>
          )}
          <div className="flex items-center gap-2">
            <TeamName name={home} />
            <span className="text-zinc-300 dark:text-zinc-600 text-xs shrink-0 font-medium">vs</span>
            <TeamName name={away} />
          </div>
        </div>
        {children && (
          <div className="shrink-0 flex items-center gap-2">
            {children}
          </div>
        )}
      </div>
    </div>
  );
}
