"use client";

import { ConfidenceBar } from "./ConfidenceBar";
import { SaveButton } from "./SaveButton";
import type { Prediction } from "@/lib/api";
import { Clock, TrendingUp, Share2 } from "lucide-react";
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

// Fallback brand colors for when no image is found
const TEAM_COLORS: Record<string, string> = {
  "Arsenal": "#EF0107", "Liverpool": "#C8102E", "Chelsea": "#034694",
  "Manchester City": "#6CABDD", "Manchester United": "#DA291C", "Tottenham": "#132257",
  "Bayern Munich": "#DC052D", "Borussia Dortmund": "#FDE100", "Real Madrid": "#FEBE10",
  "Barcelona": "#A50044", "PSG": "#004170", "Juventus": "#000000",
  "AC Milan": "#FB090B", "Inter Milan": "#010E80", "Napoli": "#087AC2",
  "Brazil": "#009C3B", "Argentina": "#74ACDF", "France": "#0055A4",
  "Germany": "#000000", "Spain": "#AA151B", "Italy": "#009246",
  "Portugal": "#006600", "England": "#CF081F", "Nigeria": "#008751",
};

function hashColor(name: string): string {
  const P = ["#6366f1","#8b5cf6","#ec4899","#f97316","#eab308",
             "#22c55e","#14b8a6","#3b82f6","#06b6d4","#ef4444"];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return P[h % P.length];
}

function getTeamAssets(name: string): { imageUrl: string; isFlag: boolean; color: string } {
  const code = COUNTRY_CODES[name];
  if (code) return {
    imageUrl: `https://flagcdn.com/w320/${code}.png`,
    isFlag: true,
    color: TEAM_COLORS[name] || hashColor(name),
  };
  // try partial match for country
  const countryKey = Object.keys(COUNTRY_CODES).find(k =>
    name.toLowerCase().includes(k.toLowerCase()) || k.toLowerCase().includes(name.toLowerCase())
  );
  if (countryKey) return {
    imageUrl: `https://flagcdn.com/w320/${COUNTRY_CODES[countryKey]}.png`,
    isFlag: true,
    color: TEAM_COLORS[name] || hashColor(name),
  };

  const id = CLUB_IDS[name];
  if (id) return {
    imageUrl: `https://crests.football-data.org/${id}.png`,
    isFlag: false,
    color: TEAM_COLORS[name] || hashColor(name),
  };
  // partial match for clubs
  const clubKey = Object.keys(CLUB_IDS).find(k =>
    name.toLowerCase().includes(k.toLowerCase()) || k.toLowerCase().includes(name.toLowerCase())
  );
  if (clubKey) return {
    imageUrl: `https://crests.football-data.org/${CLUB_IDS[clubKey]}.png`,
    isFlag: false,
    color: TEAM_COLORS[name] || hashColor(name),
  };

  return { imageUrl: "", isFlag: false, color: TEAM_COLORS[name] || hashColor(name) };
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

const TIP_CODE_COLORS: Record<string, string> = {
  "1":  "bg-white/20 text-white border-white/30",
  "2":  "bg-white/20 text-white border-white/30",
  "X":  "bg-white/20 text-white border-white/30",
  "1X": "bg-white/20 text-white border-white/30",
  "2X": "bg-white/20 text-white border-white/30",
  "?":  "bg-black/20 text-white/60 border-white/10",
};

const GOALS_TYPE_COLORS: Record<string, string> = {
  Banker: "bg-green-400/30 text-green-200 border-green-400/40",
  Asian:  "bg-yellow-400/30 text-yellow-200 border-yellow-400/30",
  Skip:   "bg-white/10 text-white/40 border-white/10",
};

/** Semicircular confidence gauge — 0 to 180° arc */
function ConfidenceGauge({ value }: { value: number }) {
  const pct   = Math.min(Math.max(value, 0), 100);
  const r     = 28;
  const cx    = 36;
  const cy    = 36;
  const circ  = Math.PI * r;                     // half circumference
  const dash  = (pct / 100) * circ;
  const color = pct >= 70 ? "#4ade80" : pct >= 50 ? "#facc15" : "#f87171";

  return (
    <div className="flex flex-col items-center">
      <svg width="72" height="40" viewBox="0 0 72 40">
        {/* Track */}
        <path
          d={`M${cx - r},${cy} A${r},${r} 0 0 1 ${cx + r},${cy}`}
          fill="none" stroke="rgba(255,255,255,0.12)" strokeWidth="6"
          strokeLinecap="round"
        />
        {/* Fill */}
        <path
          d={`M${cx - r},${cy} A${r},${r} 0 0 1 ${cx + r},${cy}`}
          fill="none" stroke={color} strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circ}`}
          style={{ transition: "stroke-dasharray 0.6s ease" }}
        />
      </svg>
      <p className="text-base font-black -mt-1" style={{ color }}>{pct}%</p>
      <p className="text-[9px] text-white/40 uppercase tracking-wide">confidence</p>
    </div>
  );
}

export function PredictionCard({ prediction: p, savedKeys, onClick }: Props) {
  const emptySet = new Set<string>();
  const gconf = Math.round(p.goals_confidence * 100);
  const isSkip = p.tip_goals === "Skip";

  const home = getTeamAssets(p.home);
  const away = getTeamAssets(p.away);

  // bg-size: flags fill their half, logos are contained with padding
  const homeSize = home.isFlag ? "cover" : "65%";
  const awaySize = away.isFlag ? "cover" : "65%";

  return (
    <div
      onClick={onClick}
      className={clsx(
        "relative overflow-hidden rounded-xl border border-white/10 flex flex-col gap-4 card-glow transition-all duration-200",
        onClick && "cursor-pointer hover:brightness-110 active:scale-[0.98]"
      )}
    >
      {/* ── Background layers ── */}

      {/* Base solid split */}
      <div className="absolute inset-0 flex">
        <div className="flex-1" style={{ background: home.color }} />
        <div className="flex-1" style={{ background: away.color }} />
      </div>

      {/* Home image — fades right */}
      {home.imageUrl && (
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: `url(${home.imageUrl})`,
            backgroundSize: homeSize,
            backgroundPosition: home.isFlag ? "left center" : "35% center",
            backgroundRepeat: "no-repeat",
            maskImage: "linear-gradient(to right, black 0%, black 25%, transparent 70%)",
            WebkitMaskImage: "linear-gradient(to right, black 0%, black 25%, transparent 70%)",
          }}
        />
      )}

      {/* Away image — fades left */}
      {away.imageUrl && (
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: `url(${away.imageUrl})`,
            backgroundSize: awaySize,
            backgroundPosition: away.isFlag ? "right center" : "65% center",
            backgroundRepeat: "no-repeat",
            maskImage: "linear-gradient(to left, black 0%, black 25%, transparent 70%)",
            WebkitMaskImage: "linear-gradient(to left, black 0%, black 25%, transparent 70%)",
          }}
        />
      )}

      {/* Dark overlay for readability */}
      <div className="absolute inset-0 bg-black/55" />

      {/* ── Card content ── */}
      <div className="relative z-10 p-4 flex flex-col gap-3">

        {/* Header */}
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-semibold text-white/70 truncate max-w-[60%] drop-shadow">
            {p.flag} {p.league_name}
          </span>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-[10px] text-white/50">
              {p.date}{p.time && p.time !== "TBD" ? ` · ${localTime(p.date, p.time)}` : ""}
            </span>
            <SaveButton prediction={p} savedKeys={savedKeys ?? emptySet} size={13} />
            <button
              onClick={e => { e.stopPropagation(); shareMatch(p); }}
              className="text-white/50 hover:text-white transition-colors"
              title="Share this pick"
            >
              <Share2 size={13} />
            </button>
          </div>
        </div>

        {/* Teams — home on top, away below, odds on right */}
        <div className="space-y-1">
          {/* Home */}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 min-w-0 flex-1">
              {home.imageUrl && (
                <img src={home.imageUrl} alt={p.home}
                  className={clsx("shrink-0 object-contain", home.isFlag ? "w-5 h-3.5 rounded-sm" : "w-4 h-4 rounded-full bg-white/10 p-0.5")}
                  onError={e => { (e.target as HTMLImageElement).style.display = "none"; }} />
              )}
              <span className="text-sm font-black text-white truncate drop-shadow">{p.home}</span>
            </div>
            {(p as any).odds_home && <span className="text-[11px] font-black bg-white/15 text-white px-1.5 py-0.5 rounded-md shrink-0">{(p as any).odds_home}</span>}
            <span className="text-sm font-bold text-white shrink-0">{Math.round(p.p_home * 100)}%</span>
          </div>
          <div className="h-1.5 bg-black/30 rounded-full overflow-hidden">
            <div className="h-full bg-white/70 rounded-full" style={{ width: `${Math.round(p.p_home * 100)}%` }} />
          </div>

          {/* Draw — smaller, between the two teams */}
          <div className="flex items-center justify-between gap-2 py-0.5">
            <span className="text-[10px] text-white/40 font-medium">Draw</span>
            <div className="flex-1 h-1 bg-black/20 rounded-full overflow-hidden mx-2">
              <div className="h-full bg-white/25 rounded-full" style={{ width: `${Math.round(p.p_draw * 100)}%` }} />
            </div>
            <span className="text-[10px] text-white/40 shrink-0">{Math.round(p.p_draw * 100)}%</span>
          </div>

          {/* Away */}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 min-w-0 flex-1">
              {away.imageUrl && (
                <img src={away.imageUrl} alt={p.away}
                  className={clsx("shrink-0 object-contain", away.isFlag ? "w-5 h-3.5 rounded-sm" : "w-4 h-4 rounded-full bg-white/10 p-0.5")}
                  onError={e => { (e.target as HTMLImageElement).style.display = "none"; }} />
              )}
              <span className="text-sm font-black text-white truncate drop-shadow">{p.away}</span>
            </div>
            {(p as any).odds_away && <span className="text-[11px] font-black bg-white/15 text-white px-1.5 py-0.5 rounded-md shrink-0">{(p as any).odds_away}</span>}
            <span className="text-sm font-bold text-white shrink-0">{Math.round(p.p_away * 100)}%</span>
          </div>
          <div className="h-1.5 bg-black/30 rounded-full overflow-hidden">
            <div className="h-full bg-white/70 rounded-full" style={{ width: `${Math.round(p.p_away * 100)}%` }} />
          </div>
        </div>

        {/* Best pick */}
        <div className="bg-black/30 border border-white/15 rounded-xl px-3 py-2.5 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] text-white/50 uppercase tracking-wide font-semibold">Best pick</p>
            <p className="text-sm font-bold text-white truncate">{p.tip_1x2}</p>
            {!isSkip && p.tip_goals && p.tip_goals !== "Skip" && (
              <p className="text-[10px] text-green-300 mt-0.5">{p.tip_goals}</p>
            )}
          </div>
          <div className="text-right shrink-0">
            <p className="text-[10px] text-white/50">Confidence</p>
            <p className={clsx("text-xl font-black", gconf >= 65 ? "text-green-400" : gconf >= 50 ? "text-yellow-400" : "text-white")}>{gconf}%</p>
          </div>
        </div>
      </div>
    </div>
  );
}
