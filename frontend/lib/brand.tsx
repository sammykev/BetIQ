// The BetIQ mark (public/logo.svg) and the share card built on it, for the
// generated images (opengraph-image, /api/og) — they can't load /public files.

export const INK = "#070b14";
export const LIME = "#b8f53d";
export const CHALK = "#f1f5f9";

/** The Q of IQ, its tail a tick. `tile` adds the rounded ink square behind
 * it; without one, the gap where the tick crosses the Q is see-through. */
export function markSvg(tile = true): string {
  const tick = `d="M261 318 311 368 421 214" fill="none" stroke-linecap="round" stroke-linejoin="round"`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">` +
    `<defs><mask id="cut"><rect width="512" height="512" fill="#fff"/><path ${tick} stroke="#000" stroke-width="96"/></mask></defs>` +
    (tile ? `<rect width="512" height="512" rx="116" fill="${INK}"/>` : "") +
    `<circle cx="215" cy="256" r="124" fill="none" stroke="${CHALK}" stroke-width="48" mask="url(#cut)"/>` +
    `<path ${tick} stroke="${LIME}" stroke-width="48"/></svg>`;
}

export const markDataUrl = (tile = true) => `data:image/svg+xml;base64,${btoa(markSvg(tile))}`;

async function googleFont(family: string, weight: 400 | 600 | 800, text?: string) {
  const q = `family=${family.replace(/ /g, "+")}:wght@${weight}${text ? `&text=${text}` : ""}`;
  const css = await (await fetch(`https://fonts.googleapis.com/css2?${q}`)).text();
  const url = css.match(/src: url\((.+?)\) format\('(?:opentype|truetype)'\)/)?.[1];
  if (!url) throw new Error(`no ${family} ${weight}`);
  return { name: family, data: await (await fetch(url)).arrayBuffer(), weight, style: "normal" as const };
}

/** The site's fonts for ImageResponse `fonts`: Inter for text, Barlow
 * Condensed ExtraBold (just the wordmark's letters) for "BETIQ". Every text
 * then names its font — otherwise the renderer borrows the wordmark face for
 * any letter it has. Empty when Google Fonts can't be reached: the image then
 * uses the default font throughout. */
export async function brandFonts() {
  try {
    return await Promise.all([googleFont("Inter", 400), googleFont("Inter", 600),
                              googleFont("Barlow Condensed", 800, "BETIQ")]);
  } catch {
    return [];
  }
}

/** Mark + "BETIQ" in a row, as in the app's header. */
export function Lockup({ size }: { size: number }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: size * 0.22 }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={markDataUrl(false)} width={size} height={size} alt="" />
      <div style={{ display: "flex", fontFamily: "Barlow Condensed", fontSize: size * 1.02, fontWeight: 800,
        lineHeight: 1, letterSpacing: size * 0.01 }}>
        <span style={{ color: CHALK }}>BET</span>
        <span style={{ color: LIME }}>IQ</span>
      </div>
    </div>
  );
}

/** The site's link preview (1200×630). */
export function ShareCard() {
  return (
    <div style={{
      background: INK, width: "100%", height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", fontFamily: "Inter", position: "relative",
    }}>
      <div style={{
        position: "absolute", width: 720, height: 720, borderRadius: "50%", top: -40, left: 240,
        background: "radial-gradient(circle, rgba(184,245,61,0.10) 0%, rgba(184,245,61,0) 65%)",
      }} />
      <Lockup size={150} />
      <div style={{ color: "#94a3b8", fontSize: 28, letterSpacing: 6, textTransform: "uppercase", marginTop: 28, marginBottom: 44 }}>
        AI Football Predictions
      </div>
      <div style={{ display: "flex", gap: 14 }}>
        {["Model-rated picks", "Value bets", "SportyBet codes", "Slip optimizer"].map(t => (
          <div key={t} style={{
            border: "1.5px solid rgba(184,245,61,0.35)", borderRadius: 100, padding: "10px 22px",
            color: LIME, fontSize: 20, fontWeight: 600,
          }}>{t}</div>
        ))}
      </div>
      <div style={{ position: "absolute", bottom: 36, color: "#475569", fontSize: 20, letterSpacing: 1 }}>
        predict-withbetiq.vercel.app
      </div>
    </div>
  );
}
