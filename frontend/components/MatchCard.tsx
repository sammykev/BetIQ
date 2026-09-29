"use client";

import { useState } from "react";
import { getTeamAssets } from "@/lib/teamAssets";
import { useTeamLogo } from "@/lib/useTeamLogo";
import { CompetitionBadge } from "./CompetitionBadge";
import { MatchBleed } from "./MatchBleed";
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
  const [broken, setBroken] = useState(false);
  const { imageUrl, isFlag, color } = getTeamAssets(name);
  // Falls back to the generic /api/team-logo lookup when the static flag/crest
  // map doesn't know this team — covers leagues and rare national teams not
  // hand-maintained in lib/teamAssets.ts.
  const dynamicImage = useTeamLogo(name, !imageUrl);
  const image = imageUrl || dynamicImage;
  const useFlagFit = imageUrl ? isFlag : false;
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      {image && !broken ? (
        <span className="relative inline-flex items-center justify-center w-5 h-5 rounded-full bg-n-800 ring-1 ring-n-800/80 overflow-hidden shrink-0">
          <img
            src={image}
            alt={name}
            className={clsx("object-contain", useFlagFit ? "w-full h-full object-cover" : "w-[70%] h-[70%]")}
            onError={() => setBroken(true)}
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
      <span className="text-n-0 font-semibold text-sm truncate">{name}</span>
    </div>
  );
}

export function MatchCard({ home, away, date, time, league, flag, children, onClick, className }: Props) {
  return (
    <div
      onClick={onClick}
      className={clsx(onClick ? "card-interactive" : "card", "relative overflow-hidden", className)}
    >
      <MatchBleed home={home} away={away} />
      <div className="relative z-10 px-4 py-3 flex items-center gap-3">
        <div className="flex-1 min-w-0 space-y-1.5">
          {(league || flag) && (
            <p className="flex items-center gap-1 text-[10px] text-n-500 font-semibold uppercase tracking-wide truncate">
              {flag && <CompetitionBadge name={league || ""} fallbackEmoji={flag} size={11} />}
              <span className="truncate">
                {league}{date ? ` · ${date}${time && time !== "TBD" ? ` ${time}` : ""}` : ""}
              </span>
            </p>
          )}
          {/* Stacked on phones so long names aren't truncated side by side */}
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
            <TeamName name={home} />
            <span className="hidden sm:inline text-n-500 text-xs shrink-0 font-medium">vs</span>
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
