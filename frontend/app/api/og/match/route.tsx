import { ImageResponse } from "next/og";
import { NextRequest } from "next/server";

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl;
  const home = searchParams.get("home") || "Home Team";
  const away = searchParams.get("away") || "Away Team";
  const tip  = searchParams.get("tip")  || "Prediction";
  const conf = searchParams.get("conf") || "0";
  const league = searchParams.get("league") || "";
  const flag   = searchParams.get("flag")   || "⚽";
  const confNum = Math.round(Number(conf) * 100);

  const color = confNum >= 80 ? "#22c55e" : confNum >= 65 ? "#eab308" : "#94a3b8";

  return new ImageResponse(
    (
      <div style={{
        background: "#0f172a",
        width: "100%", height: "100%",
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        fontFamily: "system-ui, sans-serif",
        padding: "48px",
      }}>
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 32 }}>
          <span style={{ fontSize: 28, fontWeight: 900, color: "#22c55e" }}>BetIQ</span>
          <span style={{ color: "#334155", fontSize: 20 }}>·</span>
          <span style={{ color: "#64748b", fontSize: 20 }}>{flag} {league}</span>
        </div>

        {/* Teams */}
        <div style={{ display: "flex", alignItems: "center", gap: 32, marginBottom: 32 }}>
          <div style={{ textAlign: "center" }}>
            <div style={{ color: "#f1f5f9", fontSize: 52, fontWeight: 900, maxWidth: 320 }}>{home}</div>
            <div style={{ color: "#64748b", fontSize: 18, marginTop: 4 }}>Home</div>
          </div>
          <div style={{ color: "#334155", fontSize: 40, fontWeight: 900 }}>vs</div>
          <div style={{ textAlign: "center" }}>
            <div style={{ color: "#f1f5f9", fontSize: 52, fontWeight: 900, maxWidth: 320 }}>{away}</div>
            <div style={{ color: "#64748b", fontSize: 18, marginTop: 4 }}>Away</div>
          </div>
        </div>

        {/* Tip */}
        <div style={{
          background: "rgba(34,197,94,0.1)", border: "2px solid rgba(34,197,94,0.4)",
          borderRadius: 16, padding: "16px 40px", marginBottom: 24,
          display: "flex", alignItems: "center", gap: 16,
        }}>
          <span style={{ color, fontSize: 28, fontWeight: 900 }}>🎯 {tip}</span>
        </div>

        {/* Confidence */}
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 200, height: 8, background: "#1e293b", borderRadius: 4, overflow: "hidden", display: "flex" }}>
            <div style={{ width: `${confNum}%`, height: "100%", background: color, borderRadius: 4 }} />
          </div>
          <span style={{ color, fontSize: 22, fontWeight: 700 }}>{confNum}% confidence</span>
        </div>

        {/* Footer */}
        <div style={{ position: "absolute", bottom: 32, color: "#334155", fontSize: 16 }}>
          predict-withbetiq.vercel.app · AI Football Predictions
        </div>
      </div>
    ),
    { width: 1200, height: 630 }
  );
}
