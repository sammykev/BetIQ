import { ImageResponse } from "next/og";
import { NextRequest } from "next/server";
import { INK, LIME, Lockup, brandFonts } from "@/lib/brand";
import { fetchSharedTicket } from "@/lib/share";

const SHOWN = 5;
const STATUS_COLOR: Record<string, string> = { won: LIME, lost: "#ef4444", void: "#94a3b8" };

export async function GET(req: NextRequest) {
  const t = await fetchSharedTicket(req.nextUrl.searchParams.get("id") || "");
  const legs = t?.legs ?? [];
  const status = t?.status === "won" ? "WON" : t?.status === "lost" ? "LOST" : t?.status === "void" ? "VOID" : null;

  return new ImageResponse(
    (
      <div style={{
        background: INK, width: "100%", height: "100%", display: "flex", flexDirection: "column",
        fontFamily: "Inter", padding: "48px 56px",
      }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 28 }}>
          <Lockup size={40} />
          <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
            {status && <span style={{ color: STATUS_COLOR[t!.status], fontSize: 28, fontWeight: 900 }}>{status}</span>}
            {t?.total_odds ? <span style={{ color: "#f1f5f9", fontSize: 52, fontWeight: 900 }}>{t.total_odds.toFixed(2)}x</span> : null}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10, flex: 1 }}>
          {legs.slice(0, SHOWN).map((l, i) => (
            <div key={i} style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              background: "#0f172a", borderRadius: 12, padding: "12px 20px",
              borderLeft: `6px solid ${STATUS_COLOR[l.status] ?? "#334155"}`,
            }}>
              <div style={{ display: "flex", flexDirection: "column", maxWidth: 820 }}>
                <span style={{ color: "#f1f5f9", fontSize: 24, fontWeight: 700 }}>{l.home} v {l.away}</span>
                <span style={{ color: "#94a3b8", fontSize: 18 }}>{l.label || l.marketName || l.market}</span>
              </div>
              {l.odds ? <span style={{ color: LIME, fontSize: 26, fontWeight: 900 }}>{Number(l.odds).toFixed(2)}</span> : null}
            </div>
          ))}
          {legs.length > SHOWN && (
            <span style={{ color: "#64748b", fontSize: 20 }}>+{legs.length - SHOWN} more</span>
          )}
          {!t && <span style={{ color: "#94a3b8", fontSize: 32 }}>Ticket not found</span>}
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 16 }}>
          <span style={{ color: "#f1f5f9", fontSize: 30, fontWeight: 900, letterSpacing: 6, fontFamily: "Inter" }}>{t?.code ?? ""}</span>
          <span style={{ color: "#334155", fontSize: 18 }}>predict-withbetiq.vercel.app · 18+</span>
        </div>
      </div>
    ),
    { width: 1200, height: 630, fonts: await brandFonts() }
  );
}
