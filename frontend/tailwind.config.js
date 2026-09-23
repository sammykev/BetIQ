/** @type {import('tailwindcss').Config} */
const config = {
  // Theme is a class on <html>: "dark" (default when the OS prefers dark or
  // the user chose it) or absent for light. See components/ThemeToggle.tsx.
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
          // 600+ are dark enough to read as text on white (light mode)
          600: "#4d7c0f",
          700: "#3f6212",
          800: "#365314",
          900: "#1c2a07",
          DEFAULT: "#b8f53d",
        },
        // Static navy-tinted greys. Components that pair light and dark
        // variants (`text-zinc-900 dark:text-white`) use these.
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
        // Theme-aware neutrals: the same class reads correctly in both themes
        // (n-0 = primary text, n-800 = borders/tracks, …). Values live in
        // globals.css; dark matches the zinc scale above, light mirrors it.
        n: Object.fromEntries(
          ["0", "50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"]
            .map(k => [k, `rgb(var(--n-${k}) / <alpha-value>)`])
        ),
        // Page background. Not "base": that would collide with the text-base font size.
        canvas: "rgb(var(--canvas) / <alpha-value>)",
        surface: {
          DEFAULT: "rgb(var(--surface) / <alpha-value>)",
          raised: "rgb(var(--surface-raised) / <alpha-value>)",
          sunken: "rgb(var(--surface-sunken) / <alpha-value>)",
        },
        // Lime for text and thin indicators: bright on dark, deep on light.
        // Filled lime (tickets, buttons) stays brand-400 with ink text.
        accent: "rgb(var(--accent) / <alpha-value>)",
        warn: "rgb(var(--warn) / <alpha-value>)",
        danger: "rgb(var(--danger) / <alpha-value>)",
        info: "rgb(var(--info) / <alpha-value>)",
        // Always near-black: text on lime fills, dark badges and backdrops
        ink: "#070b14",
        slate: {
          950: "#020617",
        },
      },
      boxShadow: {
        card: "var(--shadow-card)",
        "card-hover": "var(--shadow-card-hover)",
        pop: "var(--shadow-pop)",
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
