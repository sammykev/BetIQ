"use client";

import { ConfidenceBar } from "./ConfidenceBar";
import { SaveButton } from "./SaveButton";
import type { Prediction } from "@/lib/api";
import { Clock, TrendingUp, Share2 } from "lucide-react";
import clsx from "clsx";

const BASE = "https://predict-withbetiq.vercel.app";

// Team brand colors — national teams + major clubs
const TEAM_COLORS: Record<string, string> = {
  // ── National teams ──
  "Algeria": "#006233", "Angola": "#CC0000", "Argentina": "#74ACDF",
  "Australia": "#FFCD00", "Austria": "#ED2939", "Belgium": "#000000",
  "Bolivia": "#D52B1E", "Brazil": "#009C3B", "Bulgaria": "#009B77",
  "Cameroon": "#007A5E", "Canada": "#FF0000", "Chile": "#D52B1E",
  "China": "#DE2910", "Colombia": "#FCD116", "Costa Rica": "#002B7F",
  "Croatia": "#FF0000", "Czech Republic": "#D7141A", "Denmark": "#C60C30",
  "Ecuador": "#FFD100", "Egypt": "#CE1126", "England": "#CF081F",
  "Ethiopia": "#078930", "Finland": "#003580", "France": "#0055A4",
  "Germany": "#000000", "Ghana": "#FCD116", "Greece": "#0D5EAF",
  "Hungary": "#CE2939", "Iceland": "#003897", "India": "#FF9933",
  "Iran": "#239F40", "Ireland": "#009A44", "Israel": "#0038B8",
  "Italy": "#009246", "Ivory Coast": "#F77F00", "Jamaica": "#000000",
  "Japan": "#BC002D", "Kenya": "#006600", "Mali": "#009A00",
  "Mexico": "#006847", "Morocco": "#C1272D", "Netherlands": "#FF6600",
  "New Zealand": "#00247D", "Nigeria": "#008751", "Norway": "#EF2B2D",
  "Paraguay": "#D52B1E", "Peru": "#D91023", "Poland": "#DC143C",
  "Portugal": "#006600", "Qatar": "#8D1B3D", "Romania": "#002B7F",
  "Russia": "#D52B1E", "Saudi Arabia": "#006C35", "Scotland": "#003087",
  "Senegal": "#00853F", "Serbia": "#C6363C", "Slovakia": "#0B4EA2",
  "South Africa": "#007A4D", "South Korea": "#CD2E3A", "Spain": "#AA151B",
  "Sweden": "#006AA7", "Switzerland": "#FF0000", "Tunisia": "#E70013",
  "Turkey": "#E30A17", "Ukraine": "#005BBB", "United States": "#002868",
  "Uruguay": "#5EB6E4", "Venezuela": "#CF142B", "Wales": "#C8102E",
  "Uzbekistan": "#1EB53A", "Zimbabwe": "#006400", "Zambia": "#198A00",
  "Tanzania": "#1EB53A", "Uganda": "#000000", "Sudan": "#D21034",

  // ── Premier League ──
  "Arsenal": "#EF0107", "Aston Villa": "#95BFE5", "Brentford": "#E30613",
  "Brighton": "#0057B8", "Burnley": "#6C1D45", "Chelsea": "#034694",
  "Crystal Palace": "#1B458F", "Everton": "#003399", "Fulham": "#000000",
  "Ipswich": "#0044A9", "Leicester": "#003090", "Liverpool": "#C8102E",
  "Luton": "#F78F1E", "Manchester City": "#6CABDD", "Manchester United": "#DA291C",
  "Newcastle": "#241F20", "Nottm Forest": "#DD0000", "Sheffield Utd": "#EE2737",
  "Tottenham": "#132257", "West Ham": "#7A263A", "Wolves": "#FDB913",
  "Bournemouth": "#DA291C", "Southampton": "#D71920", "Sunderland": "#EB172B",

  // ── Serie A ──
  "AC Milan": "#FB090B", "Atalanta": "#1E6BB0", "Bologna": "#1A1A75",
  "Cagliari": "#990000", "Empoli": "#1B50A0", "Fiorentina": "#4F2683",
  "Frosinone": "#FDB827", "Genoa": "#8B0000", "Inter Milan": "#010E80",
  "Internazionale": "#010E80", "Juventus": "#000000", "Lazio": "#87CEEB",
  "Lecce": "#D4AF37", "Milan": "#FB090B", "Monza": "#E30613",
  "Napoli": "#087AC2", "Roma": "#8B0000", "Salernitana": "#8B1A1A",
  "Sassuolo": "#00843D", "Torino": "#8B0000", "Udinese": "#000000",
  "Verona": "#002D62", "Venezia": "#000000", "Parma": "#FFDD00",

  // ── Bundesliga ──
  "Augsburg": "#BA3733", "Bayer Leverkusen": "#E32221", "Bayern Munich": "#DC052D",
  "Bochum": "#004F9E", "Borussia Dortmund": "#FDE100", "Darmstadt": "#0075BF",
  "Eintracht Frankfurt": "#E1000F", "Freiburg": "#D00027", "Gladbach": "#000000",
  "Hamburg": "#002147", "Hoffenheim": "#1763A6", "Köln": "#FF0000",
  "Mainz": "#C3121C", "RB Leipzig": "#DD0741", "Schalke": "#00408A",
  "Stuttgart": "#E32219", "Union Berlin": "#EB1923", "Wolfsburg": "#65B32E",
  "Werder Bremen": "#009C4A", "Heidenheim": "#DD0000",

  // ── La Liga ──
  "Athletic Club": "#EE2523", "Atletico Madrid": "#CB3524", "Barcelona": "#A50044",
  "Betis": "#00954C", "Cadiz": "#FFCD00", "Celta Vigo": "#95C5E3",
  "Getafe": "#005998", "Girona": "#CD1515", "Granada": "#E02020",
  "Las Palmas": "#FFD700", "Mallorca": "#DC052D", "Osasuna": "#C8102E",
  "Rayo Vallecano": "#CE1126", "Real Madrid": "#FEBE10", "Real Sociedad": "#005AA0",
  "Sevilla": "#D40511", "Valencia": "#FF7F00", "Villarreal": "#FFC800",
  "Alaves": "#005BAC", "Espanyol": "#0070B8",

  // ── Ligue 1 ──
  "Brest": "#E30613", "Clermont": "#D40000", "Le Havre": "#0054A6",
  "Lens": "#FFCD00", "Lille": "#DA291C", "Lorient": "#F7941D",
  "Lyon": "#1D3785", "Marseille": "#009FE3", "Metz": "#7B1FA2",
  "Monaco": "#E4312B", "Montpellier": "#F05A22", "Nantes": "#FFCD00",
  "Nice": "#C8102E", "PSG": "#004170", "Reims": "#CF081F",
  "Rennes": "#CC0000", "Strasbourg": "#003189", "Toulouse": "#702F8A",

  // ── Primeira Liga ──
  "Benfica": "#E20E17", "Braga": "#CC0000", "Porto": "#1C5FA8",
  "Sporting CP": "#006839", "Vitoria": "#006633",
};

// Deterministic color from team name for unknown teams
function hashColor(name: string): string {
  const PALETTE = [
    "#6366f1","#8b5cf6","#ec4899","#f97316","#eab308",
    "#22c55e","#14b8a6","#3b82f6","#06b6d4","#ef4444",
    "#a855f7","#f59e0b","#10b981","#0ea5e9","#e11d48",
  ];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

function teamColor(name: string): string {
  // Try exact match first, then partial match
  if (TEAM_COLORS[name]) return TEAM_COLORS[name];
  const key = Object.keys(TEAM_COLORS).find(k => name.toLowerCase().includes(k.toLowerCase()) || k.toLowerCase().includes(name.toLowerCase()));
  return key ? TEAM_COLORS[key] : hashColor(name);
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

export function PredictionCard({ prediction: p, savedKeys, onClick }: Props) {
  const emptySet = new Set<string>();
  const gconf = Math.round(p.goals_confidence * 100);
  const isSkip = p.tip_goals === "Skip";

  const hc = teamColor(p.home);
  const ac = teamColor(p.away);
  const gradient = `linear-gradient(135deg, ${hc}dd 0%, ${hc}88 35%, ${ac}88 65%, ${ac}dd 100%)`;
  const borderColor = `${hc}60`;

  return (
    <div
      onClick={onClick}
      style={{ background: gradient, borderColor }}
      className={clsx(
        "border rounded-xl p-4 flex flex-col gap-4 card-glow transition-all duration-200",
        onClick && "cursor-pointer hover:brightness-110 active:scale-[0.98]"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-white/80 truncate max-w-[55%] drop-shadow">
          {p.flag} {p.league_name}
        </span>
        <div className="flex items-center gap-2 shrink-0">
          <div className="flex items-center gap-1 text-[11px] text-white/60">
            <Clock size={10} />
            <span>{p.date}{p.time !== "TBD" ? ` · ${p.time}` : ""}</span>
          </div>
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

      {/* Teams */}
      <div className="text-center py-1">
        <p className="text-base font-black text-white drop-shadow-md tracking-tight leading-snug">{p.home}</p>
        <p className="text-[10px] text-white/50 my-1 uppercase tracking-widest font-medium">vs</p>
        <p className="text-base font-black text-white drop-shadow-md tracking-tight leading-snug">{p.away}</p>
      </div>

      {/* 1X2 probability row */}
      <div className="grid grid-cols-3 gap-2 text-center">
        {[
          { label: "Home", value: p.p_home },
          { label: "Draw", value: p.p_draw },
          { label: "Away", value: p.p_away },
        ].map(({ label, value }) => (
          <div key={label} className="bg-black/25 backdrop-blur-sm rounded-lg px-2 py-2 border border-white/10">
            <p className="text-[10px] text-white/60 mb-1">{label}</p>
            <p className="text-sm font-bold text-white">{Math.round(value * 100)}%</p>
          </div>
        ))}
      </div>

      {/* Tips + confidence */}
      <div className="space-y-2.5 flex-1">
        <div className="flex items-center justify-between">
          <span className="text-xs text-white/60">1X2 Pick</span>
          <span className={clsx("text-xs font-semibold px-2.5 py-0.5 rounded-full border", TIP_CODE_COLORS[p.tip_code] || TIP_CODE_COLORS["?"])}>
            {p.tip_1x2}
          </span>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-xs text-white/60">Goals Pick</span>
          <span className={clsx("text-xs font-semibold px-2.5 py-0.5 rounded-full border", GOALS_TYPE_COLORS[p.goals_type] || GOALS_TYPE_COLORS["Skip"])}>
            {p.tip_goals}
          </span>
        </div>

        <div className="pt-1">
          {isSkip ? (
            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-white/40">Confidence</span>
                <span className="text-white/40">—</span>
              </div>
              <div className="h-1.5 bg-white/10 rounded-full" />
            </div>
          ) : (
            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-white/60">Confidence</span>
                <span className="text-white font-semibold">{gconf}%</span>
              </div>
              <div className="h-1.5 bg-black/30 rounded-full overflow-hidden">
                <div className="h-full bg-white/80 rounded-full" style={{ width: `${gconf}%` }} />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="flex gap-3 pt-2 border-t border-white/15">
        <div className="flex-1 text-center">
          <p className="text-[10px] text-white/50">Over 1.5</p>
          <p className="text-sm font-semibold text-white">{Math.round(p.p_over15 * 100)}%</p>
        </div>
        <div className="w-px bg-white/15" />
        <div className="flex-1 text-center">
          <p className="text-[10px] text-white/50">Over 2.5</p>
          <p className="text-sm font-semibold text-white">{Math.round(p.p_over25 * 100)}%</p>
        </div>
        <div className="w-px bg-white/15" />
        <div className="flex-1 text-center">
          <TrendingUp size={12} className={clsx("mx-auto mb-0.5", isSkip ? "text-white/30" : "text-green-300")} />
          <p className={clsx("text-sm font-semibold", isSkip ? "text-white/30" : "text-green-300")}>
            {isSkip ? "—" : `${gconf}%`}
          </p>
        </div>
      </div>
    </div>
  );
}
