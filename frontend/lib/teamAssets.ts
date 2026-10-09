// Shared team color/flag/logo lookup — used across all match card components

const COUNTRY_CODES: Record<string, string> = {
  "Algeria":"dz","Angola":"ao","Argentina":"ar","Australia":"au","Austria":"at",
  "Belgium":"be","Bolivia":"bo","Brazil":"br","Bulgaria":"bg","Cameroon":"cm",
  "Canada":"ca","Chile":"cl","China":"cn","Colombia":"co","Costa Rica":"cr",
  "Croatia":"hr","Czech Republic":"cz","Denmark":"dk","Ecuador":"ec","Egypt":"eg",
  "England":"gb-eng","Ethiopia":"et","Finland":"fi","France":"fr","Germany":"de",
  "Ghana":"gh","Greece":"gr","Hungary":"hu","Iceland":"is","India":"in","Iran":"ir",
  "Ireland":"ie","Israel":"il","Italy":"it","Ivory Coast":"ci","Côte d'Ivoire":"ci",
  "Jamaica":"jm","Japan":"jp","Kenya":"ke","Mali":"ml","Mexico":"mx","Morocco":"ma",
  "Netherlands":"nl","New Zealand":"nz","Nigeria":"ng","Northern Ireland":"gb-nir",
  "Norway":"no","Paraguay":"py","Peru":"pe","Poland":"pl","Portugal":"pt","Qatar":"qa",
  "Romania":"ro","Russia":"ru","Saudi Arabia":"sa","Scotland":"gb-sct","Senegal":"sn",
  "Serbia":"rs","Slovakia":"sk","Slovenia":"si","South Africa":"za","South Korea":"kr",
  "Spain":"es","Sweden":"se","Switzerland":"ch","Tunisia":"tn","Turkey":"tr",
  "Ukraine":"ua","United States":"us","Uruguay":"uy","Uzbekistan":"uz","Venezuela":"ve",
  "Wales":"gb-wls","Zimbabwe":"zw","Zambia":"zm","Tanzania":"tz","Uganda":"ug",
  "Sudan":"sd","Libya":"ly","Guinea":"gn","Mozambique":"mz","Rwanda":"rw","Togo":"tg",
  "Benin":"bj","Burkina Faso":"bf","Niger":"ne","Chad":"td","DR Congo":"cd",
  "Congo":"cg","Gabon":"ga","Equatorial Guinea":"gq","Cape Verde":"cv",
  "Mauritania":"mr","Gambia":"gm","Sierra Leone":"sl","Liberia":"lr",
  "Bosnia-Herzegovina":"ba","Bosnia and Herzegovina":"ba","Bosnia & Herzegovina":"ba",
  "Bosnia":"ba","Kosovo":"xk",
};

// Club crests come from the server (football-data.org's own team lists,
// matched by whole words: /api/team-logos) or the fixture feed — never a
// hand-kept list of ids, which went stale and put wrong crests on clubs.

const TEAM_COLORS: Record<string, string> = {
  "Arsenal":"#EF0107","Liverpool":"#C8102E","Chelsea":"#034694",
  "Manchester City":"#6CABDD","Manchester United":"#DA291C","Tottenham":"#132257",
  "Bayern Munich":"#DC052D","Borussia Dortmund":"#FDE100","Real Madrid":"#FEBE10",
  "Barcelona":"#A50044","PSG":"#004170","Juventus":"#000000",
  "AC Milan":"#FB090B","Inter Milan":"#010E80","Napoli":"#087AC2",
  "Brazil":"#009C3B","Argentina":"#74ACDF","France":"#0055A4","Germany":"#000000",
  "Spain":"#AA151B","Italy":"#009246","Portugal":"#006600","England":"#CF081F",
  "Nigeria":"#008751","Algeria":"#006233","Morocco":"#C1272D","Ghana":"#FCD116",
  "Senegal":"#00853F","Cameroon":"#007A5E","South Africa":"#007A4D",
  "Scotland":"#003087","Wales":"#C8102E","Qatar":"#8D1B3D",
  "Bosnia-Herzegovina":"#003087","Bosnia and Herzegovina":"#003087",
};

function hashColor(name: string): string {
  const P = ["#6366f1","#8b5cf6","#ec4899","#f97316","#eab308",
             "#22c55e","#14b8a6","#3b82f6","#06b6d4","#ef4444"];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return P[h % P.length];
}

export interface TeamAssets {
  imageUrl: string;
  isFlag:   boolean;
  color:    string;
}

const words = (s: string): string[] =>
  s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

// A key's word is found in a name's word: the same word, or (for longer
// words) the start of one ("Hamburg" in "Hamburger SV")
const wordHit = (k: string, n: string) => n === k || (k.length >= 5 && n.startsWith(k));

/**
 * The map key a team name stands for, by whole words, never substrings:
 * "Mali" is not in "Somalia", "Roma" is not "Romania". Every word of the key
 * must be in the name; the key matching the most words wins, then the one
 * that comes first in the name — so "RCD Espanyol de Barcelona" is Espanyol,
 * not Barcelona. Failing that, a short name that is part of exactly one key
 * ("Bayern" → "Bayern Munich").
 */
export function matchKey(name: string, keys: string[]): string | undefined {
  const nw = words(name);
  if (!nw.length) return undefined;
  let best: { key: string; n: number; at: number } | undefined;
  for (const key of keys) {
    const kw = words(key);
    if (!kw.length) continue;
    const at = kw.map(k => nw.findIndex(n => wordHit(k, n)));
    if (at.some(i => i < 0)) continue;
    const first = Math.min(...at);
    if (!best || kw.length > best.n || (kw.length === best.n && first < best.at)) best = { key, n: kw.length, at: first };
  }
  if (best) return best.key;
  const wider = keys.filter(key => { const kw = words(key); return nw.every(n => kw.includes(n)); });
  return wider.length === 1 ? wider[0] : undefined;
}

const COUNTRY_KEYS = Object.keys(COUNTRY_CODES);

// Crests the fixtures came with (football-data.org's own, by exact team
// name): trusted over any guess from the maps below
const KNOWN_CRESTS = new Map<string, string>();

/** Remember the crests a list of predictions carries (home_crest/away_crest). */
export function rememberCrests(preds: { home?: string; away?: string; home_crest?: string | null; away_crest?: string | null }[]): void {
  for (const p of preds) {
    if (p.home && p.home_crest) KNOWN_CRESTS.set(p.home, p.home_crest);
    if (p.away && p.away_crest) KNOWN_CRESTS.set(p.away, p.away_crest);
  }
}

export function getTeamAssets(name: string): TeamAssets {
  const color = TEAM_COLORS[name] || hashColor(name);
  const crest = KNOWN_CRESTS.get(name);
  if (crest) return { imageUrl: crest, isFlag: false, color };
  const country = COUNTRY_CODES[name] ? name : matchKey(name, COUNTRY_KEYS);
  if (country) return { imageUrl: `https://flagcdn.com/w320/${COUNTRY_CODES[country]}.png`, isFlag: true, color };

  return { imageUrl: "", isFlag: false, color };
}

export function getMatchGradient(home: string, away: string): string {
  const h = getTeamAssets(home);
  const a = getTeamAssets(away);
  return `linear-gradient(135deg, ${h.color}cc 0%, ${h.color}66 35%, ${a.color}66 65%, ${a.color}cc 100%)`;
}
