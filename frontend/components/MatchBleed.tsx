"use client";

import { getTeamAssets } from "@/lib/teamAssets";
import { useTeamLogo } from "@/lib/useTeamLogo";

function useBleedAsset(name: string) {
  const asset = getTeamAssets(name);
  // Same fallback chain as the team badges on the card itself — the static
  // map only covers curated leagues/countries, everything else (World Cup
  // qualifiers, smaller clubs) comes from the generic backend lookup. The
  // hook's own cache means this doesn't cost an extra request beyond what
  // the badge in the card header already triggers.
  const dynamicImage = useTeamLogo(name, !asset.imageUrl);
  return { image: asset.imageUrl || dynamicImage, isFlag: asset.imageUrl ? asset.isFlag : false, color: asset.color };
}

/**
 * Faint team-color/logo wash behind a match card's content — each side's
 * color and crest/flag fade in from its edge and blend together in the
 * middle, echoing the old dark full-bleed card design but toned down to sit
 * quietly behind today's light-first card surface instead of replacing it.
 * Render as the first child of a `relative overflow-hidden` container, with
 * the real content in a `relative z-10` wrapper above it.
 */
export function MatchBleed({ home, away }: { home: string; away: string }) {
  const h = useBleedAsset(home);
  const a = useBleedAsset(away);

  return (
    <div className="absolute inset-0 pointer-events-none" aria-hidden="true">
      <div
        className="absolute inset-0 opacity-[0.05] dark:opacity-[0.09]"
        style={{
          background: `linear-gradient(90deg, ${h.color} 0%, transparent 48%, transparent 52%, ${a.color} 100%)`,
        }}
      />
      {h.image && (
        <div
          className="absolute inset-y-0 left-0 w-2/3 opacity-[0.06] dark:opacity-[0.12]"
          style={{
            backgroundImage: `url(${h.image})`,
            backgroundSize: h.isFlag ? "cover" : "45%",
            backgroundPosition: h.isFlag ? "left center" : "15% center",
            backgroundRepeat: "no-repeat",
            maskImage: "linear-gradient(to right, black 0%, black 15%, transparent 85%)",
            WebkitMaskImage: "linear-gradient(to right, black 0%, black 15%, transparent 85%)",
          }}
        />
      )}
      {a.image && (
        <div
          className="absolute inset-y-0 right-0 w-2/3 opacity-[0.06] dark:opacity-[0.12]"
          style={{
            backgroundImage: `url(${a.image})`,
            backgroundSize: a.isFlag ? "cover" : "45%",
            backgroundPosition: a.isFlag ? "right center" : "85% center",
            backgroundRepeat: "no-repeat",
            maskImage: "linear-gradient(to left, black 0%, black 15%, transparent 85%)",
            WebkitMaskImage: "linear-gradient(to left, black 0%, black 15%, transparent 85%)",
          }}
        />
      )}
    </div>
  );
}
