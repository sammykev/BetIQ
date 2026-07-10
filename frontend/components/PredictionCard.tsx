"use client";

import { useState } from "react";
import { SaveButton } from "./SaveButton";
import { useTeamLogo } from "@/lib/useTeamLogo";
import { CompetitionBadge } from "./CompetitionBadge";
import type { Prediction } from "@/lib/api";
import { Share2, Sparkles } from "lucide-react";
import clsx from "clsx";

const BASE = "https://predict-withbetiq.vercel.app";

/** Convert a UTC "HH:MM" match time to the user's local timezone. */
function localTime(date: string, utcTime: string): string {
  try {
    const dt = new Date(`${date}T${utcTime}:00Z`);
    return dt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return utcTime;
  }
}

/** "2026-07-11" → "Sat 11 Jul" */
function shortDate(date: string): string {
  try {
    return new Date(`${date}T00:00:00`).toLocaleDateString(undefined, {
      weekday: "short", day: "numeric", month: "short",
    });
  } catch {
    return date;
  }
}

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
  const text = `⚽ ${p.home} vs ${p.away}\n🎯 Tip: ${p.tip_1x2} (${Math.round(p.goals_confidence * 100)}% confidence)\n\nvia BetIQ — AI Football Predictions\n${BASE}`;
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
        className="relative inline-flex items-center justify-center rounded-full bg-zinc-100 dark:bg-zinc-800 ring-1 ring-zinc-200/80 dark:ring-zinc-700 overflow-hidden shrink-0"
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
      className="inline-flex items-center justify-center rounded-full text-white font-bold shrink-0"
      style={{ width: size, height: size, background: asset.color, fontSize: size * 0.4 }}
    >
      {name.slice(0, 2).toUpperCase()}
    </span>
  );
}

function TeamRow({ name, prob, odds, strongest }: {
  name: string; prob: number; odds?: number; strongest: boolean;
}) {
  return (
    <div className="flex items-center gap-2.5 min-w-0">
      <TeamBadge name={name} size={28} />
      <span className={clsx(
        "flex-1 truncate text-sm",
        strongest ? "font-bold text-zinc-900 dark:text-white" : "font-medium text-zinc-600 dark:text-zinc-300"
      )}>
        {name}
      </span>
      {odds != null && odds > 0 && (
        <span className="tnum text-[11px] font-semibold text-zinc-400 dark:text-zinc-500 bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 rounded-md shrink-0">
          {odds}
        </span>
      )}
      <span className={clsx(
        "tnum text-sm w-10 text-right shrink-0",
        strongest ? "font-bold text-zinc-900 dark:text-white" : "font-medium text-zinc-400 dark:text-zinc-500"
      )}>
        {Math.round(prob * 100)}%
      </span>
    </div>
  );
}

export function PredictionCard({ prediction: p, savedKeys, onClick }: Props) {
  const emptySet = new Set<string>();
  const gconf = Math.round(p.goals_confidence * 100);

  const h = Math.round(p.p_home * 100);
  const d = Math.round(p.p_draw * 100);
  const a = Math.max(0, 100 - h - d);
  const maxP = Math.max(p.p_home, p.p_draw, p.p_away);

  return (
    <article
      onClick={onClick}
      className={clsx("card p-4 flex flex-col gap-3.5", onClick && "card-interactive")}
    >
      {/* Header: league + date */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <CompetitionBadge name={p.league_name} fallbackEmoji={p.flag} size={14} className="text-sm" />
          <span className="text-[11px] font-semibold text-zinc-400 dark:text-zinc-500 uppercase tracking-wide truncate">
            {p.league_name}
          </span>
          {p.is_value_bet && (
            <span className="bg-brand-600 text-white text-[9px] font-black px-1.5 py-0.5 rounded-full uppercase tracking-wider shrink-0">
              Value
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[11px] tnum text-zinc-400 dark:text-zinc-500 font-medium">
            {shortDate(p.date)}{p.time && p.time !== "TBD" ? ` · ${localTime(p.date, p.time)}` : ""}
          </span>
          <SaveButton prediction={p} savedKeys={savedKeys ?? emptySet} size={14} />
          <button
            onClick={e => { e.stopPropagation(); shareMatch(p); }}
            className="text-zinc-300 dark:text-zinc-600 hover:text-zinc-500 dark:hover:text-zinc-400 transition-colors"
            title="Share this pick"
          >
            <Share2 size={14} />
          </button>
        </div>
      </div>

      {/* Teams */}
      <div className="space-y-2">
        <TeamRow name={p.home} prob={p.p_home} odds={p.odds_home} strongest={p.p_home === maxP} />
        <TeamRow name={p.away} prob={p.p_away} odds={p.odds_away} strongest={p.p_away === maxP} />
      </div>

      {/* Segmented 1X2 probability bar */}
      <div>
        <div className="flex h-1.5 rounded-full overflow-hidden gap-px bg-zinc-100 dark:bg-zinc-800">
          <div className="bg-brand-500 rounded-l-full" style={{ width: `${h}%` }} />
          <div className="bg-zinc-300 dark:bg-zinc-600" style={{ width: `${d}%` }} />
          <div className="bg-sky-500 rounded-r-full" style={{ width: `${a}%` }} />
        </div>
        <div className="flex justify-between mt-1.5 text-[10px] tnum font-medium text-zinc-400 dark:text-zinc-500">
          <span><span className="text-brand-600 dark:text-brand-400 font-bold">1</span> {h}%</span>
          <span><span className="font-bold">X</span> {d}%</span>
          <span><span className="text-sky-600 dark:text-sky-400 font-bold">2</span> {a}%</span>
        </div>
      </div>

      {/* Best pick footer */}
      <div className="flex items-center justify-between gap-3 bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-100 dark:border-zinc-800 rounded-xl px-3 py-2.5 mt-auto">
        <div className="min-w-0">
          <p className="text-[10px] text-zinc-400 dark:text-zinc-500 uppercase tracking-wider font-semibold flex items-center gap-1">
            <Sparkles size={9} /> Best pick
          </p>
          <p className="text-sm font-bold text-zinc-900 dark:text-white truncate">{p.tip_1x2}</p>
          {p.tip_goals && p.tip_goals !== "Skip" && (
            <p className="text-[11px] text-brand-600 dark:text-brand-400 font-medium mt-0.5 truncate">{p.tip_goals}</p>
          )}
        </div>
        <div className="text-right shrink-0">
          <p className="text-[10px] text-zinc-400 dark:text-zinc-500 uppercase tracking-wider font-semibold">Conf.</p>
          <p className={clsx(
            "tnum text-lg font-black leading-tight",
            gconf >= 65 ? "text-brand-600 dark:text-brand-400" : gconf >= 50 ? "text-amber-500" : "text-zinc-400"
          )}>
            {gconf}%
          </p>
        </div>
        {p.is_value_bet && p.value_edge != null && (
          <div className="shrink-0 bg-brand-50 dark:bg-brand-900/30 border border-brand-200 dark:border-brand-800 rounded-lg px-2 py-1 text-center">
            <p className="text-[9px] text-brand-700 dark:text-brand-400 font-bold uppercase tracking-wide">Edge</p>
            <p className="tnum text-sm font-black text-brand-600 dark:text-brand-300">+{Math.round(p.value_edge * 100)}%</p>
          </div>
        )}
      </div>
    </article>
  );
}
