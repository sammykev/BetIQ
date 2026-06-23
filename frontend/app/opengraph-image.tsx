import { ImageResponse } from "next/og";

export const runtime = "edge";
export const alt = "BetIQ — AI Football Predictions";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default async function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          background: "linear-gradient(135deg, #020817 0%, #0f172a 50%, #020817 100%)",
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
        {/* Green glow accent */}
        <div style={{
          position: "absolute",
          width: 400,
          height: 400,
          borderRadius: "50%",
          background: "radial-gradient(circle, rgba(34,197,94,0.15) 0%, transparent 70%)",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
        }} />

        {/* Football */}
        <div style={{ fontSize: 96, marginBottom: 16 }}>⚽</div>

        {/* BetIQ wordmark */}
        <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginBottom: 16 }}>
          <span style={{ color: "#f1f5f9", fontSize: 96, fontWeight: 900, letterSpacing: -4 }}>Bet</span>
          <span style={{ color: "#22c55e", fontSize: 96, fontWeight: 900, letterSpacing: -4 }}>IQ</span>
        </div>

        {/* Tagline */}
        <div style={{
          color: "#64748b",
          fontSize: 30,
          fontWeight: 400,
          letterSpacing: 4,
          textTransform: "uppercase",
          marginBottom: 40,
        }}>
          AI Football Predictions
        </div>

        {/* Feature pills */}
        <div style={{ display: "flex", gap: 16 }}>
          {["XGBoost Model", "Elo Ratings", "SportyBet Codes", "9 Leagues"].map(f => (
            <div key={f} style={{
              background: "rgba(34,197,94,0.1)",
              border: "1px solid rgba(34,197,94,0.3)",
              borderRadius: 100,
              padding: "8px 20px",
              color: "#22c55e",
              fontSize: 18,
              fontWeight: 600,
            }}>
              {f}
            </div>
          ))}
        </div>

        {/* URL bar */}
        <div style={{
          position: "absolute",
          bottom: 40,
          color: "#334155",
          fontSize: 20,
          letterSpacing: 1,
        }}>
          predict-withbetiq.vercel.app
        </div>
      </div>
    ),
    { ...size }
  );
}
