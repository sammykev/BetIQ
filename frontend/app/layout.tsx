import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BetIQ — Smart Football Predictions",
  description: "AI-powered football predictions using XGBoost + Elo ratings across 9 major leagues.",
  icons: { icon: "/favicon.svg" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
