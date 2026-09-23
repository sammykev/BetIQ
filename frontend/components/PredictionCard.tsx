"use client";

import { useState } from "react";
import { SaveButton } from "./SaveButton";
import { useBetSlip } from "@/lib/useBetSlip";
import { isSelected, selectionFromPrediction } from "@/lib/slip";
import { useTeamLogo } from "@/lib/useTeamLogo";
import { CompetitionBadge } from "./CompetitionBadge";
import { MatchBleed } from "./MatchBleed";
import type { Prediction } from "@/lib/api";
import { kickoff, localTime } from "@/lib/matchTime";
import { confidenceTier, headlinePick, pickProbability, type HeadlinePick } from "@/lib/picks";
import { Check, Plus, Share2 } from "lucide-react";
import clsx from "clsx";

const BASE = "https://predict-withbetiq.vercel.app";

// ── Country name → ISO 3166-1 alpha-2 code (flagcdn.com) ─────────────────
const COUNTRY_CODES: Record<string, string> = {
  "Algeria": "dz", "Angola": "ao", "Argentina": "ar", "Australia": "au",
  "Austria": "at", "Belgium": "be", "Bolivia": "bo", "Brazil": "br",
  "Bulgaria": "bg", "Cameroon": "cm", "Canada": "ca", "Chile": "cl",
  "China": "cn", "Colombia": "co", "Costa Rica": "cr", "Croatia": "hr",
  "Czech Republic": "cz", "Denmark": "dk", "Ecuador": "ec", "Egypt": "eg",
  "England": "gb-eng", "Ethiopia": "et", "Finland": "fi", "France": "fr",
  "Germany": "de", "Ghana": "gh", "Greece": "gr", "Hungary": "hu",
  "Iceland": "is", "India": "in", "Iran": "ir", "Ireland": "ie",
  "Israel": "il", "Italy": "it", "Ivory Coast": "ci", "Jamaica": "jm",
  "Japan": "jp", "Kenya": "ke", "Mali": "ml", "Mexico": "mx",
  "Morocco": "ma", "Netherlands": "nl", "New Zealand": "nz", "Nigeria": "ng",
  "Northern Ireland": "gb-nir", "Norway": "no", "Paraguay": "py",
  "Peru": "pe", "Poland": "pl", "Portugal": "pt", "Qatar": "qa",
  "Romania": "ro", "Russia": "ru", "Saudi Arabia": "sa", "Scotland": "gb-sct",
  "Senegal": "sn", "Serbia": "rs", "Slovakia": "sk", "Slovenia": "si",
  "South Africa": "za", "South Korea": "kr", "Spain": "es", "Sweden": "se",
  "Switzerland": "ch", "Tunisia": "tn", "Turkey": "tr", "Ukraine": "ua",
  "United States": "us", "Uruguay": "uy", "Uzbekistan": "uz",
  "Venezuela": "ve", "Wales": "gb-wls", "Zimbabwe": "zw", "Zambia": "zm",
  "Tanzania": "tz", "Uganda": "ug", "Sudan": "sd", "Libya": "ly",
  "Guinea": "gn", "Mozambique": "mz", "Rwanda": "rw", "Togo": "tg",
  "Benin": "bj", "Burkina Faso": "bf", "Niger": "ne", "Chad": "td",
  "DR Congo": "cd", "Congo": "cg", "Gabon": "ga", "Equatorial Guinea": "gq",
  "Cape Verde": "cv", "Mauritania": "mr", "Gambia": "gm", "Sierra Leone": "sl",
  "Liberia": "lr", "Côte d'Ivoire": "ci",
};

// ── Club name → football-data.org team ID ─────────────────────────────────
const CLUB_IDS: Record<string, number> = {
  // Premier League
  "Arsenal": 57, "Aston Villa": 58, "Brentford": 402, "Brighton": 397,
  "Burnley": 328, "Chelsea": 61, "Crystal Palace": 354, "Everton": 62,
  "Fulham": 63, "Ipswich": 349, "Leicester": 338, "Liverpool": 64,
  "Luton": 389, "Manchester City": 65, "Manchester United": 66,
  "Newcastle": 67, "Nottm Forest": 351, "Sheffield Utd": 356,
  "Southampton": 340, "Tottenham": 73, "West Ham": 563, "Wolves": 76,
  "Bournemouth": 1044, "Sunderland": 356,
  // Serie A
  "AC Milan": 98, "Atalanta": 102, "Bologna": 103, "Cagliari": 488,
  "Empoli": 445, "Fiorentina": 99, "Frosinone": 7398, "Genoa": 107,
  "Inter Milan": 108, "Internazionale": 108, "Juventus": 109, "Lazio": 110,
  "Lecce": 5890, "Milan": 98, "Monza": 5911, "Napoli": 113,
  "Roma": 100, "Salernitana": 5915, "Sassuolo": 471, "Torino": 586,
  "Udinese": 115, "Verona": 450, "Venezia": 454, "Parma": 112,
  // Bundesliga
  "Augsburg": 16, "Bayer Leverkusen": 3, "Bayern Munich": 5,
  "Bochum": 29, "Borussia Dortmund": 4, "Darmstadt": 6,
  "Eintracht Frankfurt": 9, "Freiburg": 8, "Gladbach": 18,
  "Hamburg": 20, "Heidenheim": 10267, "Hoffenheim": 720,
  "Köln": 1, "Mainz": 15, "RB Leipzig": 721,
  "Stuttgart": 10, "Union Berlin": 28, "Wolfsburg": 11,
  "Werder Bremen": 13,
  // La Liga
  "Athletic Club": 77, "Atletico Madrid": 78, "Barcelona": 81,
  "Betis": 90, "Cadiz": 8634, "Celta Vigo": 558, "Espanyol": 80,
  "Getafe": 82, "Girona": 298, "Granada": 83, "Las Palmas": 275,
  "Mallorca": 89, "Osasuna": 79, "Rayo Vallecano": 87, "Real Madrid": 86,
  "Real Sociedad": 92, "Sevilla": 559, "Valencia": 95, "Villarreal": 94,
  "Alaves": 263,
  // Ligue 1
  "Brest": 532, "Clermont": 528, "Le Havre": 539, "Lens": 6911,
  "Lille": 521, "Lorient": 537, "Lyon": 523, "Marseille": 516,
  "Metz": 527, "Monaco": 548, "Montpellier": 527, "Nantes": 543,
  "Nice": 522, "PSG": 524, "Paris Saint-Germain": 524,
  "Reims": 547, "Rennes": 529, "Strasbourg": 576, "Toulouse": 576,
  // Primeira Liga
  "Benfica": 1903, "Braga": 5602, "Porto": 503, "Sporting CP": 498,
};

function hashColor(name: string): string {
  const P = ["#6366f1","#8b5cf6","#ec4899","#f97316","#eab308",
             "#22c55e","#14b8a6","#3b82f6","#06b6d4","#ef4444"];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return P[h % P.length];
}

export function getTeamAssets(name: string): { imageUrl: string; isFlag: boolean; color: string } {
  const code = COUNTRY_CODES[name];
  if (code) return {
    imageUrl: `https://flagcdn.com/w320/${code}.png`,
    isFlag: true,
    color: hashColor(name),
  };
  // try partial match for country
  const countryKey = Object.keys(COUNTRY_CODES).find(k =>
    name.toLowerCase().includes(k.toLowerCase()) || k.toLowerCase().includes(name.toLowerCase())
  );
  if (countryKey) return {
    imageUrl: `https://flagcdn.com/w320/${COUNTRY_CODES[countryKey]}.png`,
    isFlag: true,
    color: hashColor(name),
  };

  const id = CLUB_IDS[name];
  if (id) return {
    imageUrl: `https://crests.football-data.org/${id}.png`,
    isFlag: false,
    color: hashColor(name),
  };
  // partial match for clubs
  const clubKey = Object.keys(CLUB_IDS).find(k =>
    name.toLowerCase().includes(k.toLowerCase()) || k.toLowerCase().includes(name.toLowerCase())
  );
  if (clubKey) return {
    imageUrl: `https://crests.football-data.org/${CLUB_IDS[clubKey]}.png`,
    isFlag: false,
    color: hashColor(name),
  };

  return { imageUrl: "", isFlag: false, color: hashColor(name) };
}

function shareMatch(p: Prediction) {
  const prob = pickProbability(p);
  const tip = prob !== null ? `${p.tip_1x2} (${Math.round(prob * 100)}% probability)` : p.tip_1x2;
  const text = `⚽ ${p.home} vs ${p.away}\n🎯 Tip: ${tip}\n\nvia BetIQ — AI Football Predictions\n${BASE}`;
  if (navigator.share) {
    navigator.share({ title: "BetIQ Pick", text, url: BASE }).catch(() => {});
  } else {
    navigator.clipboard.writeText(text).then(() => alert("Pick copied to clipboard!")).catch(() => {});
  }
}

interface Props {
  prediction: Prediction;
  savedKeys?: Set<string>;
  onClick?: () => void;
  /** False when the card sits under a day heading — show only the time. */
  showDay?: boolean;
  /** Called after the star saves/unsaves this pick. */
  onSaveToggle?: (key: string, saved: boolean) => void;
}

/** Crest or flag avatar with a monogram fallback. */
export function TeamBadge({ name, size = 28 }: { name: string; size?: number }) {
  const [broken, setBroken] = useState(false);
  const asset = getTeamAssets(name);
  // Static flag/crest maps only cover ~10 leagues + a curated country list.
  // Any team outside those (smaller leagues, newly-added competitions, rare
  // World Cup qualifiers not yet added to the flag map) falls back to the
  // generic /api/team-logo lookup — same mechanism already used for
  // basketball, works for any team in any league without hardcoding.
  const dynamicImage = useTeamLogo(name, !asset.imageUrl);
  const image = asset.imageUrl || dynamicImage;
  const isFlag = asset.imageUrl ? asset.isFlag : false;

  if (image && !broken) {
    return (
      <span
        className="relative inline-flex items-center justify-center rounded-full bg-n-800 ring-1 ring-n-700/80 overflow-hidden shrink-0"
        style={{ width: size, height: size }}
      >
        <img
          src={image}
          alt={name}
          className={clsx("object-contain", isFlag ? "w-full h-full object-cover" : "w-[70%] h-[70%]")}
          onError={() => setBroken(true)}
        />
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center justify-center rounded-full text-white font-bold shrink-0 ring-1 ring-white/10"
      style={{ width: size, height: size, background: asset.color, fontSize: size * 0.4 }}
    >
      {name.slice(0, 2).toUpperCase()}
    </span>
  );
}

/** One side of the scoreboard: crest, name, bookmaker odds, model probability. */
function TeamRow({ name, prob, odds, favourite }: {
  name: string; prob: number; odds?: number; favourite: boolean;
}) {
  return (
    <div className="flex items-center gap-3 min-w-0">
      <TeamBadge name={name} size={30} />
      <span className={clsx(
        "flex-1 truncate text-[15px]",
        favourite ? "font-bold text-n-0" : "font-semibold text-n-400"
      )}>
        {name}
      </span>
      {odds != null && odds > 0 && (
        <span className="font-mono text-[11px] text-n-500 shrink-0" title="Bookmaker odds">
          {odds.toFixed(2)}
        </span>
      )}
      <span className={clsx(
        "font-display font-extrabold text-[30px] leading-none w-[3.25rem] text-right shrink-0 tnum",
        favourite ? "text-n-0" : "text-n-500"
      )}>
        {Math.round(prob * 100)}<span className="text-[15px] font-bold text-n-500 align-top ml-px">%</span>
      </span>
    </div>
  );
}

/**
 * The model's pick, styled as a bet ticket. Solid lime for a strong pick,
 * outlined amber for a lean, muted below that — judged for the kind of pick
 * (a 55% straight win is strong; a 55% double chance isn't). The number is
 * the probability of *this* pick; the goals tip, if any, sits underneath
 * with its own probability.
 */
function PickTicket({ pick, edge }: { pick: HeadlinePick; edge?: number | null }) {
  const pct = pick.prob === null ? null : Math.round(pick.prob * 100);
  const tier = pick.prob === null ? "weak" : confidenceTier(pick.prob, pick.kind);
  const strong = tier === "strong";
  const lean = tier === "lean";
  return (
    <div className={clsx(
      "relative flex items-center gap-3 rounded-xl px-3.5 py-3",
      strong ? "bg-brand-400 text-ink"
        : lean ? "bg-amber-400/[0.06] border border-amber-400/30 text-n-0"
        : "bg-n-800/60 border border-n-700/60 text-n-0"
    )}>
      {edge != null && edge > 0.05 && (
        <span className="absolute -top-2.5 right-3 font-mono text-[10px] font-bold bg-ink text-brand-400 border border-brand-400/70 rounded-md px-1.5 py-0.5">
          +{Math.round(edge * 100)}% EDGE
        </span>
      )}
      <div className="min-w-0 flex-1">
        <p className={clsx("font-display font-bold text-[11px] uppercase tracking-[0.14em]", strong ? "text-ink/60" : "text-n-400")}>
          Best pick
        </p>
        <p className="font-display font-extrabold text-xl uppercase leading-tight truncate">{pick.label}</p>
        {pick.secondary && (
          <p className={clsx("text-[11px] font-semibold truncate", strong ? "text-ink/70" : "text-n-400")}>
            + {pick.secondary.label} · <span className="tnum">{Math.round(pick.secondary.prob * 100)}%</span>
          </p>
        )}
      </div>
      {pct !== null && (
        <div className="text-right shrink-0">
          <p className={clsx("font-display font-bold text-[11px] uppercase tracking-[0.14em]", strong ? "text-ink/60" : "text-n-400")}>
            Probability
          </p>
          <p className={clsx(
            "font-display font-extrabold text-[32px] leading-none tnum",
            strong ? "text-ink" : lean ? "text-warn" : "text-n-300"
          )}>
            {pct}%
          </p>
        </div>
      )}
    </div>
  );
}

/** Adds the card's 1X2 / double-chance pick to the bet slip. */
function SlipToggle({ prediction: p }: { prediction: Prediction }) {
  const { items, toggle } = useBetSlip();
  const sel = selectionFromPrediction(p);
  if (!sel) return null;
  const inSlip = isSelected(items, sel);
  return (
    <button
      onClick={e => { e.stopPropagation(); toggle(sel); }}
      aria-pressed={inSlip}
      aria-label={inSlip ? `Remove ${sel.label} from slip` : `Add ${sel.label} to slip`}
      className={clsx(
        "mt-2 w-full inline-flex items-center justify-center gap-1.5 h-8 rounded-lg text-xs font-bold uppercase tracking-wider border transition-colors",
        inSlip
          ? "border-brand-400/60 bg-brand-400/10 text-accent"
          : "border-n-800 text-n-300 hover:text-n-0 hover:border-n-600"
      )}
    >
      {inSlip ? <Check size={13} /> : <Plus size={13} />}
      {inSlip ? "In slip" : "Slip"}
    </button>
  );
}

export function PredictionCard({ prediction: p, savedKeys, onClick, showDay = true, onSaveToggle }: Props) {
  const emptySet = new Set<string>();
  const headline = headlinePick(p);

  const h = Math.round(p.p_home * 100);
  const d = Math.round(p.p_draw * 100);
  const a = Math.max(0, 100 - h - d);
  const maxP = Math.max(p.p_home, p.p_draw, p.p_away);
  const fav = p.p_home === maxP ? "1" : p.p_away === maxP ? "2" : "X";

  const segments = [
    { key: "1", width: h },
    { key: "X", width: d },
    { key: "2", width: a },
  ];

  return (
    <article
      onClick={onClick}
      className={clsx("card relative overflow-hidden flex flex-col", onClick && "card-interactive")}
    >
      <MatchBleed home={p.home} away={p.away} />

      {/* Header strip: competition + kick-off */}
      <div className="relative z-10 flex items-center justify-between gap-2 px-4 py-2.5 border-b border-n-800/80">
        <div className="flex items-center gap-1.5 min-w-0">
          <CompetitionBadge name={p.league_name} fallbackEmoji={p.flag} size={14} className="text-sm" />
          <span className="eyebrow truncate">{p.league_name}</span>
        </div>
        <div className="flex items-center gap-2.5 shrink-0">
          <span className="font-mono text-[11px] text-n-200 uppercase">
            {showDay ? kickoff(p.date, p.time) : p.time && p.time !== "TBD" ? localTime(p.date, p.time) : "TBD"}
          </span>
          <SaveButton prediction={p} savedKeys={savedKeys ?? emptySet} onToggle={onSaveToggle} size={14} />
          <button
            onClick={e => { e.stopPropagation(); shareMatch(p); }}
            className="text-n-500 hover:text-n-0 transition-colors"
            title="Share this pick"
            aria-label={`Share ${p.home} vs ${p.away}`}
          >
            <Share2 size={14} />
          </button>
        </div>
      </div>

      <div className="relative z-10 flex-1 flex flex-col px-4 pt-3.5 pb-4 gap-3">
        {/* Scoreboard */}
        <div className="space-y-2.5">
          <TeamRow name={p.home} prob={p.p_home} odds={p.odds_home} favourite={fav === "1"} />
          <TeamRow name={p.away} prob={p.p_away} odds={p.odds_away} favourite={fav === "2"} />
        </div>

        {/* 1X2 split — the favoured outcome is lit */}
        <div>
          <div className="flex h-1 rounded-full overflow-hidden gap-0.5">
            {segments.map(s => (
              <div
                key={s.key}
                className={s.key === fav ? "bg-accent" : s.key === "X" ? "bg-n-700" : "bg-n-600"}
                style={{ width: `${s.width}%` }}
              />
            ))}
          </div>
          <div className="flex justify-between mt-1.5 font-mono text-[10px] text-n-500 tnum">
            {segments.map(s => (
              <span key={s.key} className={s.key === fav ? "text-accent" : undefined}>
                {s.key} · {s.width}
              </span>
            ))}
          </div>
        </div>

        <div className="mt-auto pt-1">
          <PickTicket pick={headline} edge={p.is_value_bet ? p.value_edge : null} />
          <SlipToggle prediction={p} />
        </div>
      </div>
    </article>
  );
}
