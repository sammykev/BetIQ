"use client";

import { useState } from "react";
import { useCompetitionLogo } from "@/lib/useCompetitionLogo";
import clsx from "clsx";

interface Props {
  /** Competition/league name, e.g. "World Cup", "Premier League". */
  name: string;
  /** Emoji fallback already used throughout the app (p.flag). */
  fallbackEmoji: string;
  size?: number;
  className?: string;
  /** TheSportsDB sport taxonomy — "Soccer" (default) or "Basketball" etc. */
  sport?: string;
}

/**
 * Drop-in replacement for a bare `{p.flag}` emoji span — renders a real
 * competition badge (World Cup trophy, league crest, ...) when the generic
 * /api/competition-logo lookup finds one, otherwise falls back to the emoji
 * exactly as before. Safe to use anywhere the emoji was used inline with text.
 */
/** ESPN's dark-background version of one of its league logos, else null. */
export function darkVariant(url: string): string | null {
  const m = url.match(/^(https:\/\/a\.espncdn\.com\/i\/leaguelogos\/soccer\/)500(\/\d+\.png)$/);
  return m ? `${m[1]}500-dark${m[2]}` : null;
}

export function CompetitionBadge({ name, fallbackEmoji, size = 14, className, sport = "Soccer" }: Props) {
  const [broken, setBroken] = useState(false);
  const logo = useCompetitionLogo(name, !!name, sport);

  if (logo && !broken) {
    const img = (src: string, cls?: string) => (
      <img
        src={src}
        alt={name}
        title={name}
        className={clsx("inline-block object-contain shrink-0", cls, className)}
        style={{ width: size, height: size }}
        // Some badge hosts refuse hotlinked images based on the Referer
        referrerPolicy="no-referrer"
        onError={() => setBroken(true)}
      />
    );
    // ESPN's competition logos come in a version drawn for dark backgrounds
    const dark = darkVariant(logo);
    if (dark) return <>{img(logo, "dark:hidden")}{img(dark, "hidden dark:inline-block")}</>;
    return img(logo);
  }
  return <span className={clsx("leading-none", className)}>{fallbackEmoji}</span>;
}
