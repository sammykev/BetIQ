import type { Metadata, Viewport } from "next";
import { Inter, Barlow_Condensed, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { ClerkProvider } from "@clerk/nextjs";
import Script from "next/script";
import { themeInitScript } from "@/lib/theme";
import { ThemeSync } from "@/components/ThemeToggle";
import { BetSlipProvider } from "@/lib/useBetSlip";
import { TrafficBeacon } from "@/components/TrafficBeacon";
import { MotionProvider } from "@/components/ui/motion-provider";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

// Scoreboard face for big numbers, pick labels and eyebrows
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
  title: "BetIQ — AI Sports Predictions",
  description: "Honest probabilities for football, basketball, tennis and table tennis, graded against every final. Pick your games and get a SportyBet or football.com booking code in one tap.",
  icons: { icon: "/favicon.svg", apple: "/apple-touch-icon.png" },
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black",
    title: "BetIQ",
  },
  // The standard form of apple-mobile-web-app-capable, which Chrome now warns is deprecated
  other: { "mobile-web-app-capable": "yes" },
  openGraph: {
    title: "BetIQ — AI Sports Predictions",
    description: "Football, basketball, tennis and table tennis predictions with SportyBet and football.com booking codes.",
    url: "https://predict-withbetiq.vercel.app",
    siteName: "BetIQ",
    type: "website",
    locale: "en_NG",
    images: [{
      url: "https://predict-withbetiq.vercel.app/api/og",
      width: 1200,
      height: 630,
      alt: "BetIQ — AI Sports Predictions",
    }],
  },
  twitter: {
    card: "summary_large_image",
    title: "BetIQ — AI Sports Predictions",
    description: "AI picks + SportyBet and football.com booking codes. Bet smarter.",
    images: ["https://predict-withbetiq.vercel.app/api/og"],
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f5f7fb" },
    { media: "(prefers-color-scheme: dark)", color: "#070b14" },
  ],
  colorScheme: "light dark",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Clerk's sign-in / sign-up modals, themed to the Matchday dark palette in
    // both themes (a dark dialog reads fine over the light pages too)
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
      {/* The "dark" class is set before paint by themeInitScript; the server
          default is dark, so suppress the expected class mismatch. */}
      <html lang="en" className={`dark ${inter.variable} ${display.variable} ${mono.variable}`} suppressHydrationWarning>
        <head>
          <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        </head>
        <body className="antialiased">
          <ThemeSync />
          <TrafficBeacon />
          <MotionProvider>
            <BetSlipProvider>{children}</BetSlipProvider>
          </MotionProvider>
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
