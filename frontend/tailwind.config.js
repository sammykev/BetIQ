/** @type {import('tailwindcss').Config} */
const config = {
  // Matchday is dark-only: <html class="dark"> is set permanently in
  // app/layout.tsx, so every existing `dark:` variant always applies.
  darkMode: "class",
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "var(--font-inter)",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        // Condensed scoreboard face for headings, big numbers and labels
        display: ["var(--font-display)", "Arial Narrow", "sans-serif"],
        // Kick-off times, odds, booking codes
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        // Electric lime — the one accent. Means "pick / value / go".
        brand: {
          50: "#f7fee7",
          100: "#ecfccb",
          200: "#d9f99d",
          300: "#c8f76a",
          400: "#b8f53d",
          500: "#a3e635",
          600: "#84cc16",
          700: "#65a30d",
          800: "#3f6212",
          900: "#1c2a07",
          DEFAULT: "#b8f53d",
        },
        // zinc is re-tinted toward navy so every zinc-* utility in the app
        // lands on the Matchday surfaces without touching each component.
        zinc: {
          50: "#f1f4f9",
          100: "#e3e8f0",
          200: "#c6cfdd",
          300: "#a3aec3",
          400: "#7d8aa3",
          500: "#5f6b82",
          600: "#3b4863",
          700: "#27334b",
          800: "#1a2438",
          900: "#0f1729",
          950: "#070b14",
        },
        ink: "#070b14",
        surface: {
          DEFAULT: "#0f1729",
          raised: "#131d33",
          sunken: "#0a101d",
        },
        slate: {
          950: "#020617",
        },
      },
      boxShadow: {
        card: "inset 0 1px 0 0 rgb(255 255 255 / 0.03), 0 1px 2px 0 rgb(0 0 0 / 0.4)",
        "card-hover":
          "inset 0 1px 0 0 rgb(255 255 255 / 0.05), 0 12px 32px -12px rgb(0 0 0 / 0.7)",
        pop: "0 24px 64px -16px rgb(0 0 0 / 0.8), 0 0 0 1px rgb(255 255 255 / 0.04)",
        glow: "0 0 0 1px rgb(184 245 61 / 0.35), 0 8px 32px -8px rgb(184 245 61 / 0.35)",
      },
      animation: {
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "fade-in": "fade-in 0.3s ease both",
        "slide-up": "slide-up 0.35s cubic-bezier(0.16, 1, 0.3, 1) both",
        "scale-in": "scale-in 0.25s cubic-bezier(0.16, 1, 0.3, 1) both",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(12px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.97)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
      },
    },
  },
  plugins: [],
};

module.exports = config;
