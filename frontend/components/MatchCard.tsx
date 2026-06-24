"use client";

import { getTeamAssets, getMatchGradient } from "@/lib/teamAssets";
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
  const { imageUrl, isFlag } = getTeamAssets(name);
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      {imageUrl && (
        <img
          src={imageUrl}
          alt={name}
          className={clsx("shrink-0 object-contain",
            isFlag ? "w-6 h-4 rounded-sm" : "w-5 h-5 rounded-full bg-white/10 p-0.5")}
          onError={e => { (e.target as HTMLImageElement).style.display = "none"; }}
        />
      )}
      <span className="text-white font-bold text-sm truncate drop-shadow">{name}</span>
    </div>
  );
}

export function MatchCard({ home, away, date, time, league, flag, children, onClick, className }: Props) {
  const gradient = getMatchGradient(home, away);
  const homeA = getTeamAssets(home);
  const awayA = getTeamAssets(away);
  const homeSize = homeA.isFlag ? "cover" : "60%";
  const awaySize = awayA.isFlag ? "cover" : "60%";

  return (
    <div
      onClick={onClick}
      className={clsx(
        "relative overflow-hidden rounded-xl border border-white/10",
        onClick && "cursor-pointer hover:brightness-110 active:scale-[0.98] transition-all",
        className
      )}
    >
      {/* Background layers */}
      <div className="absolute inset-0 flex">
        <div className="flex-1" style={{ background: homeA.color }} />
        <div className="flex-1" style={{ background: awayA.color }} />
      </div>
      {homeA.imageUrl && (
        <div className="absolute inset-0" style={{
          backgroundImage: `url(${homeA.imageUrl})`,
          backgroundSize: homeSize,
          backgroundPosition: homeA.isFlag ? "left center" : "30% center",
          backgroundRepeat: "no-repeat",
          maskImage: "linear-gradient(to right, black 0%, black 20%, transparent 65%)",
          WebkitMaskImage: "linear-gradient(to right, black 0%, black 20%, transparent 65%)",
        }} />
      )}
      {awayA.imageUrl && (
        <div className="absolute inset-0" style={{
          backgroundImage: `url(${awayA.imageUrl})`,
          backgroundSize: awaySize,
          backgroundPosition: awayA.isFlag ? "right center" : "70% center",
          backgroundRepeat: "no-repeat",
          maskImage: "linear-gradient(to left, black 0%, black 20%, transparent 65%)",
          WebkitMaskImage: "linear-gradient(to left, black 0%, black 20%, transparent 65%)",
        }} />
      )}
      <div className="absolute inset-0 bg-black/55" />

      {/* Content */}
      <div className="relative z-10 px-4 py-3 flex items-center gap-3">
        <div className="flex-1 min-w-0 space-y-1.5">
          {(league || flag) && (
            <p className="text-[10px] text-white/60 font-medium truncate">
              {flag} {league}{date ? ` · ${date}${time && time !== "TBD" ? ` ${time}` : ""}` : ""}
            </p>
          )}
          <div className="flex items-center gap-2">
            <TeamName name={home} />
            <span className="text-white/40 text-xs shrink-0">vs</span>
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
