// Image URLs for basketball teams and tennis/table tennis players

// ── NBA team logos (ESPN CDN) ──────────────────────────────────────────────
const NBA_LOGOS: Record<string, string> = {
  "Atlanta Hawks": "atl", "Boston Celtics": "bos", "Brooklyn Nets": "bkn",
  "Charlotte Hornets": "cha", "Chicago Bulls": "chi", "Cleveland Cavaliers": "cle",
  "Dallas Mavericks": "dal", "Denver Nuggets": "den", "Detroit Pistons": "det",
  "Golden State Warriors": "gsw", "Houston Rockets": "hou", "Indiana Pacers": "ind",
  "LA Clippers": "lac", "LA Lakers": "lal", "Los Angeles Lakers": "lal",
  "Los Angeles Clippers": "lac", "Memphis Grizzlies": "mem", "Miami Heat": "mia",
  "Milwaukee Bucks": "mil", "Minnesota Timberwolves": "min", "New Orleans Pelicans": "nop",
  "New York Knicks": "ny", "Oklahoma City Thunder": "okc", "Orlando Magic": "orl",
  "Philadelphia 76ers": "phi", "Phoenix Suns": "phx", "Portland Trail Blazers": "por",
  "Sacramento Kings": "sac", "San Antonio Spurs": "sa", "Toronto Raptors": "tor",
  "Utah Jazz": "uta", "Washington Wizards": "was",
};

// ── EuroLeague & international basketball team colors ─────────────────────
const BBALL_COLORS: Record<string, string> = {
  "LA Lakers": "#552583", "Los Angeles Lakers": "#552583",
  "Boston Celtics": "#007A33", "Golden State Warriors": "#1D428A",
  "Miami Heat": "#98002E", "Chicago Bulls": "#CE1141",
  "New York Knicks": "#006BB6", "Philadelphia 76ers": "#006BB6",
  "Oklahoma City Thunder": "#007AC1", "Denver Nuggets": "#0E2240",
  "Milwaukee Bucks": "#00471B", "Phoenix Suns": "#1D1160",
  "Dallas Mavericks": "#00538C", "Los Angeles Clippers": "#C8102E",
  "Toronto Raptors": "#CE1141", "Brooklyn Nets": "#000000",
  "Atlanta Hawks": "#E03A3E", "Cleveland Cavaliers": "#860038",
  "Indiana Pacers": "#002D62", "Washington Wizards": "#002B5C",
  "New Orleans Pelicans": "#0C2340", "Utah Jazz": "#002B5C",
  "Sacramento Kings": "#5A2D81", "Memphis Grizzlies": "#5D76A9",
  "Minnesota Timberwolves": "#236192", "Charlotte Hornets": "#1D1160",
  "Portland Trail Blazers": "#E03A3E", "Orlando Magic": "#0077C0",
  "Detroit Pistons": "#C8102E", "San Antonio Spurs": "#C4CED4",
  "Houston Rockets": "#CE1141", "CSKA Moscow": "#CC0000",
  "Real Madrid": "#FEBE10", "Barcelona": "#A50044",
  "Anadolu Efes": "#003580", "Fenerbahce": "#003399",
  "Olympiacos": "#E30613", "Panathinaikos": "#006633",
  "Maccabi Tel Aviv": "#FFD700", "Zalgiris": "#006600",
};

// ── Tennis player photos (SofaScore CDN, no auth needed) ──────────────────
const TENNIS_PLAYER_IDS: Record<string, number> = {
  // ATP Top players
  "Novak Djokovic": 14478, "Carlos Alcaraz": 358624, "Jannik Sinner": 202607,
  "Daniil Medvedev": 64684, "Alexander Zverev": 156622, "Andrey Rublev": 164378,
  "Casper Ruud": 220573, "Stefanos Tsitsipas": 156616, "Holger Rune": 393569,
  "Taylor Fritz": 155682, "Tommy Paul": 248745, "Ben Shelton": 427023,
  "Frances Tiafoe": 283260, "Grigor Dimitrov": 50029, "Alex de Minaur": 261959,
  "Hubert Hurkacz": 222033, "Ugo Humbert": 277456, "Sebastian Korda": 347460,
  "Felix Auger-Aliassime": 303573, "Cameron Norrie": 278844,
  "Nick Kyrgios": 94406, "Rafael Nadal": 17402, "Roger Federer": 30974,
  "Andy Murray": 31366, "Stan Wawrinka": 31355,
  // WTA Top players
  "Iga Swiatek": 225980, "Aryna Sabalenka": 151518, "Elena Rybakina": 201612,
  "Coco Gauff": 306517, "Jessica Pegula": 161768, "Qinwen Zheng": 344897,
  "Caroline Wozniacki": 25991, "Barbora Krejcikova": 151524,
  "Jasmine Paolini": 245416, "Daria Kasatkina": 164393,
  "Maria Sakkari": 156621, "Beatriz Haddad Maia": 266350,
  "Victoria Azarenka": 34594, "Naomi Osaka": 230272,
  "Serena Williams": 20267, "Emma Raducanu": 350276,
  "Belinda Bencic": 110025, "Karolina Pliskova": 50032,
};

// ── Player nationality flags for table tennis fallback ────────────────────
const TABLE_TENNIS_FLAGS: Record<string, string> = {
  "Ma Long": "cn", "Fan Zhendong": "cn", "Xu Xin": "cn", "Wang Chuqin": "cn",
  "Liang Jingkun": "cn", "Chen Meng": "cn", "Sun Yingsha": "cn", "Wang Manyu": "cn",
  "Timo Boll": "de", "Dimitrij Ovtcharov": "de", "Patrick Franziska": "de",
  "Hugo Calderano": "br", "Quadri Aruna": "ng", "Tomokazu Harimoto": "jp",
  "Kenta Matsudaira": "jp", "Jun Mizutani": "jp", "Miu Hirano": "jp",
  "Lee Sangsu": "kr", "Jang Woojin": "kr", "Shin Yubin": "kr",
  "Truls Moregard": "se", "Anton Kallberg": "se",
  "Bernadette Szocs": "ro", "Elizabeta Samara": "ro",
  "Lily Zhang": "us", "Kanak Jha": "us",
};

function nbaLogoUrl(teamName: string): string {
  const abbrev = NBA_LOGOS[teamName];
  if (abbrev) return `https://a.espncdn.com/i/teamlogos/nba/500/${abbrev}.png`;
  // Try partial match
  const key = Object.keys(NBA_LOGOS).find(k => teamName.toLowerCase().includes(k.toLowerCase().split(" ").pop()!));
  if (key) return `https://a.espncdn.com/i/teamlogos/nba/500/${NBA_LOGOS[key]}.png`;
  return "";
}

function tennisPlayerImageUrl(playerName: string): string {
  // Exact match
  const id = TENNIS_PLAYER_IDS[playerName];
  if (id) return `https://api.sofascore.app/api/v1/player/${id}/image`;
  // Partial match (e.g., "C. Alcaraz" → "Carlos Alcaraz")
  const key = Object.keys(TENNIS_PLAYER_IDS).find(k => {
    const parts = k.split(" ");
    const lastName = parts[parts.length - 1];
    return playerName.toLowerCase().includes(lastName.toLowerCase());
  });
  if (key) return `https://api.sofascore.app/api/v1/player/${TENNIS_PLAYER_IDS[key]}/image`;
  return "";
}

function tableTennisImageUrl(playerName: string): string {
  // Try player face first
  const flagCode = TABLE_TENNIS_FLAGS[playerName];
  if (flagCode) return `https://flagcdn.com/w320/${flagCode}.png`;
  return "";
}

export interface SportAssets {
  homeImage: string;
  awayImage: string;
  homeColor: string;
  awayColor: string;
  isPlayerFace: boolean;  // true = face/flag blend, false = logo blend
}

/** Map our internal sport key to TheSportsDB's sport taxonomy for competition badge lookups. */
export function sportDbSport(sport: string): string {
  if (sport === "basketball") return "Basketball";
  if (sport === "tennis") return "Tennis";
  if (sport === "table_tennis") return "Table Tennis";
  return "Soccer";
}

function hashColor(name: string): string {
  const P = ["#1e40af","#7c3aed","#be123c","#b45309","#166534","#0e7490","#9d174d","#1e3a5f"];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return P[h % P.length];
}

export function getSportAssets(home: string, away: string, sport: string): SportAssets {
  if (sport === "basketball") {
    return {
      homeImage:   nbaLogoUrl(home),
      awayImage:   nbaLogoUrl(away),
      homeColor:   BBALL_COLORS[home] || hashColor(home),
      awayColor:   BBALL_COLORS[away] || hashColor(away),
      isPlayerFace: false,
    };
  }
  if (sport === "tennis") {
    return {
      homeImage:    tennisPlayerImageUrl(home),
      awayImage:    tennisPlayerImageUrl(away),
      homeColor:    hashColor(home),
      awayColor:    hashColor(away),
      isPlayerFace: true,
    };
  }
  if (sport === "table_tennis") {
    return {
      homeImage:    tableTennisImageUrl(home),
      awayImage:    tableTennisImageUrl(away),
      homeColor:    hashColor(home),
      awayColor:    hashColor(away),
      isPlayerFace: true,
    };
  }
  return { homeImage: "", awayImage: "", homeColor: hashColor(home), awayColor: hashColor(away), isPlayerFace: false };
}
