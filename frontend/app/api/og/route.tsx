import { ImageResponse } from "next/og";

export async function GET() {
  return new ImageResponse(
    (
      <div
        style={{
          background: "#020817",
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "system-ui, sans-serif",
          position: "relative",
        }}
      >
        {/* Glow */}
        <div style={{
          position: "absolute",
          width: 500,
          height: 500,
          borderRadius: "50%",
          background: "rgba(34,197,94,0.08)",
          top: "50%",
          left: "50%",
          transform: "translate(-50%,-50%)",
        }} />

        {/* Football */}
        <div style={{ fontSize: 100, marginBottom: 20 }}>⚽</div>

        {/* Wordmark */}
        <div style={{ display: "flex", alignItems: "baseline", marginBottom: 20 }}>
          <span style={{ color: "#f1f5f9", fontSize: 100, fontWeight: 900, letterSpacing: -4 }}>Bet</span>
          <span style={{ color: "#22c55e", fontSize: 100, fontWeight: 900, letterSpacing: -4 }}>IQ</span>
        </div>

        {/* Tagline */}
        <div style={{ color: "#64748b", fontSize: 28, letterSpacing: 5, textTransform: "uppercase", marginBottom: 44 }}>
          AI Football Predictions
        </div>

        {/* Pills */}
        <div style={{ display: "flex", gap: 14 }}>
          {["XGBoost + Elo", "9 Leagues", "SportyBet Codes", "Live AI Analysis"].map(t => (
            <div key={t} style={{
              background: "rgba(34,197,94,0.1)",
              border: "1px solid rgba(34,197,94,0.3)",
              borderRadius: 100,
              padding: "10px 22px",
              color: "#22c55e",
              fontSize: 18,
              fontWeight: 600,
            }}>{t}</div>
          ))}
        </div>

        {/* URL */}
        <div style={{ position: "absolute", bottom: 36, color: "#1e293b", fontSize: 20 }}>
          predict-withbetiq.vercel.app
        </div>
      </div>
    ),
    { width: 1200, height: 630 }
  );
}
