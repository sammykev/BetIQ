import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BetIQ — Smart Football Predictions",
  description: "AI-powered football predictions using XGBoost + Elo ratings across 9 major leagues.",
  icons: { icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>⚽</text></svg>" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased">{children}</body>
    </html>
  );
}
