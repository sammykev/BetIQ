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

const CLUB_IDS: Record<string, number> = {
  "Arsenal":57,"Aston Villa":58,"Brentford":402,"Brighton":397,"Burnley":328,
  "Chelsea":61,"Crystal Palace":354,"Everton":62,"Fulham":63,"Ipswich":349,
  "Leicester":338,"Liverpool":64,"Luton":389,"Manchester City":65,"Manchester United":66,
  "Newcastle":67,"Nottm Forest":351,"Sheffield Utd":356,"Southampton":340,
  "Tottenham":73,"West Ham":563,"Wolves":76,"Bournemouth":1044,"Sunderland":71,
  "AC Milan":98,"Atalanta":102,"Bologna":103,"Cagliari":488,"Empoli":445,
  "Fiorentina":99,"Frosinone":470,"Genoa":107,"Inter Milan":108,"Internazionale":108,
  "Juventus":109,"Lazio":110,"Lecce":5890,"Milan":98,"Monza":5911,"Napoli":113,
  "Roma":100,"Salernitana":5915,"Sassuolo":471,"Torino":586,"Udinese":115,
  "Verona":450,"Venezia":454,"Parma":112,"Augsburg":16,"Bayer Leverkusen":3,
  "Bayern Munich":5,"Bochum":29,"Borussia Dortmund":4,"Darmstadt":6,
  "Eintracht Frankfurt":9,"Freiburg":8,"Gladbach":18,"Hamburg":20,"Heidenheim":10267,
  "Hoffenheim":720,"Köln":1,"Mainz":15,"RB Leipzig":721,"Stuttgart":10,
  "Union Berlin":28,"Wolfsburg":11,"Werder Bremen":13,"Athletic Club":77,
  "Atletico Madrid":78,"Barcelona":81,"Betis":90,"Cadiz":8634,"Celta Vigo":558,
  "Espanyol":80,"Getafe":82,"Girona":298,"Granada":83,"Las Palmas":275,
  "Mallorca":89,"Osasuna":79,"Rayo Vallecano":87,"Real Madrid":86,
  "Real Sociedad":92,"Sevilla":559,"Valencia":95,"Villarreal":94,"Alaves":263,
  "Brest":532,"Clermont":528,"Le Havre":539,"Lens":546,"Lille":521,"Lorient":537,
  "Lyon":523,"Marseille":516,"Metz":545,"Monaco":548,"Montpellier":518,"Nantes":543,
  "Nice":522,"PSG":524,"Paris Saint-Germain":524,"Reims":547,"Rennes":529,
  "Strasbourg":576,"Toulouse":511,"Benfica":1903,"Braga":5602,"Porto":503,
  "Sporting CP":498,
};

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
const CLUB_KEYS = Object.keys(CLUB_IDS);

export function getTeamAssets(name: string): TeamAssets {
  const color = TEAM_COLORS[name] || hashColor(name);
  const country = COUNTRY_CODES[name] ? name : matchKey(name, COUNTRY_KEYS);
  if (country) return { imageUrl: `https://flagcdn.com/w320/${COUNTRY_CODES[country]}.png`, isFlag: true, color };

  const club = CLUB_IDS[name] ? name : matchKey(name, CLUB_KEYS);
  if (club) return { imageUrl: `https://crests.football-data.org/${CLUB_IDS[club]}.png`, isFlag: false, color };

  return { imageUrl: "", isFlag: false, color };
}

export function getMatchGradient(home: string, away: string): string {
  const h = getTeamAssets(home);
  const a = getTeamAssets(away);
  return `linear-gradient(135deg, ${h.color}cc 0%, ${h.color}66 35%, ${a.color}66 65%, ${a.color}cc 100%)`;
}
