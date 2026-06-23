import type { Metadata, Viewport } from "next";
import "./globals.css";
import { ClerkProvider } from "@clerk/nextjs";
import Script from "next/script";

export const metadata: Metadata = {
  title: "BetIQ — AI Football Predictions",
  description: "Get AI-powered football predictions with XGBoost + Elo ratings. Pick your games, generate a SportyBet booking code, and bet smarter across 9 major leagues.",
  icons: { icon: "/favicon.svg", apple: "/logo.svg" },
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "BetIQ",
  },
  openGraph: {
    title: "BetIQ — AI Football Predictions",
    description: "AI-powered predictions across 9 leagues. Chat to build accumulators, generate SportyBet booking codes instantly.",
    url: "https://predict-withbetiq.vercel.app",
    siteName: "BetIQ",
    type: "website",
    locale: "en_NG",
    images: [{
      url: "https://predict-withbetiq.vercel.app/opengraph-image",
      width: 1200,
      height: 630,
      alt: "BetIQ — AI Football Predictions",
    }],
  },
  twitter: {
    card: "summary_large_image",
    title: "BetIQ — AI Football Predictions",
    description: "AI picks + SportyBet booking codes. Bet smarter.",
    images: ["https://predict-withbetiq.vercel.app/opengraph-image"],
  },
};

export const viewport: Viewport = {
  themeColor: "#22c55e",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider>
      <html lang="en">
        <body className="antialiased">
          {children}
          <Script id="sw-register" strategy="afterInteractive">{`
            if ('serviceWorker' in navigator) {
              navigator.serviceWorker.register('/sw.js');
            }
          `}</Script>
        </body>
      </html>
    </ClerkProvider>
  );
}
