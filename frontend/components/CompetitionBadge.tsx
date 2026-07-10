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
}

/**
 * Drop-in replacement for a bare `{p.flag}` emoji span — renders a real
 * competition badge (World Cup trophy, league crest, ...) when the generic
 * /api/competition-logo lookup finds one, otherwise falls back to the emoji
 * exactly as before. Safe to use anywhere the emoji was used inline with text.
 */
export function CompetitionBadge({ name, fallbackEmoji, size = 14, className }: Props) {
  const [broken, setBroken] = useState(false);
  const logo = useCompetitionLogo(name, !!name);

  if (logo && !broken) {
    return (
      <img
        src={logo}
        alt={name}
        title={name}
        className={clsx("inline-block object-contain shrink-0", className)}
        style={{ width: size, height: size }}
        onError={() => setBroken(true)}
      />
    );
  }
  return <span className={clsx("leading-none", className)}>{fallbackEmoji}</span>;
}
