import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ClerkProvider } from "@clerk/nextjs";
import Script from "next/script";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

export const metadata: Metadata = {
  metadataBase: new URL("https://predict-withbetiq.vercel.app"),
  title: "BetIQ — AI Football Predictions",
  description: "Get AI-powered football predictions with XGBoost + Elo ratings. Pick your games, generate a SportyBet booking code, and bet smarter across 9 major leagues.",
  icons: { icon: "/favicon.svg", apple: "/logo.svg" },
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
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
      url: "https://predict-withbetiq.vercel.app/api/og",
      width: 1200,
      height: 630,
      alt: "BetIQ — AI Football Predictions",
    }],
  },
  twitter: {
    card: "summary_large_image",
    title: "BetIQ — AI Football Predictions",
    description: "AI picks + SportyBet booking codes. Bet smarter.",
    images: ["https://predict-withbetiq.vercel.app/api/og"],
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fafafa" },
    { media: "(prefers-color-scheme: dark)", color: "#09090b" },
  ],
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider>
      <html lang="en" className={inter.variable}>
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
