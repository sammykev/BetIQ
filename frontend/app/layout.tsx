import type { Metadata, Viewport } from "next";
import { Inter, Barlow_Condensed, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { ClerkProvider } from "@clerk/nextjs";
import Script from "next/script";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

// Scoreboard face for headings and big numbers
const display = Barlow_Condensed({
  subsets: ["latin"],
  weight: ["600", "700", "800"],
  display: "swap",
  variable: "--font-display",
});

// Kick-off times, odds and booking codes
const mono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["500", "700"],
  display: "swap",
  variable: "--font-mono",
});

export const metadata: Metadata = {
  metadataBase: new URL("https://predict-withbetiq.vercel.app"),
  title: "BetIQ — AI Football Predictions",
  description: "Get AI-powered football predictions with XGBoost + Elo ratings. Pick your games, generate a SportyBet booking code, and bet smarter across 9 major leagues.",
  icons: { icon: "/favicon.svg", apple: "/logo.svg" },
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black",
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
  themeColor: "#070b14",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Clerk's sign-in / sign-up modals, themed to match (Matchday is dark-only)
    <ClerkProvider
      appearance={{
        variables: {
          colorPrimary: "#b8f53d",
          colorPrimaryForeground: "#070b14",
          colorBackground: "#0f1729",
          colorForeground: "#e3e8f0",
          colorMuted: "#131d33",
          colorMutedForeground: "#a3aec3",
          colorInput: "#0a101d",
          colorInputForeground: "#e3e8f0",
          colorNeutral: "#e3e8f0",
          colorBorder: "#27334b",
          colorRing: "#b8f53d",
          colorModalBackdrop: "rgb(7 11 20 / 0.8)",
          borderRadius: "0.75rem",
        },
      }}
    >
      <html lang="en" className={`dark ${inter.variable} ${display.variable} ${mono.variable}`}>
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
