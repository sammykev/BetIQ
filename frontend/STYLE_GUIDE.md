# BetIQ — "Matchday" Design System & Style Guide

A sportsbook-grade identity: navy surfaces (or crisp white in light mode), one
electric-lime accent, and condensed scoreboard type for anything that should
read at a glance. Everything below already lives in the code — tokens in
`tailwind.config.js` and `app/globals.css`, fonts in `app/layout.tsx`. Reach for
the named token or component class, never a raw hex value in a component.

**Light and dark themes.** The theme is the `dark` class on `<html>`. A script
in `<head>` (`lib/theme.ts`) applies the saved choice, or the OS setting, before
first paint; `ThemeToggle` (sun/moon, top bar and landing nav) flips and saves
it; `ThemeSync` re-applies it after hydration.

**Write new components with theme tokens, not `dark:` pairs.** The `n-*`,
`canvas`, `surface`, `accent`, `warn`, `danger` and `info` colours are CSS
variables that swap with the theme, so one class reads correctly in both:
`text-n-0` is near-black on light and white on dark. Older components that
pair `text-zinc-900 dark:text-white` still work, since `zinc-*` is a static
scale, but prefer tokens.

---

## 1. Grid & spacing

Tailwind's 4px spacing scale. Stick to these rungs and avoid arbitrary values.

| Purpose | Token | Value |
| --- | --- | --- |
| Page gutters | `px-4 sm:px-6` | 16 → 24px |
| Max content width | `max-w-6xl mx-auto` | 1152px, centred |
| Section rhythm | `space-y-5` / `space-y-6` | 20–24px between blocks |
| Day groups in a list | `space-y-8` | 32px |
| Card grids | `gap-4` | 16px |
| In-card stacks | `gap-3` / `space-y-2.5` | 10–12px |

**Grid.** Card collections use one responsive grid everywhere:
`grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4`. Detail pages (match,
dashboard) use a main column plus a `360px` right rail on `lg`
(`grid lg:grid-cols-[1fr_360px]`), with the rail first in the DOM so the key
number leads on mobile. The desktop shell reserves a fixed `lg:pl-60` sidebar.

**Mobile first screen.** The first prediction must be visible without
scrolling on a 390×844 phone. Keep filters to one row each and avoid stat tiles
above the list.

---

## 2. Color

| Role | Token | Dark | Light |
| --- | --- | --- | --- |
| Page background | `bg-canvas` | `#070b14` | `#f5f7fb` |
| Card surface | `bg-surface` · `.card` | `#0f1729` | `#ffffff` |
| Raised (hover, slip) | `bg-surface-raised` | `#131d33` | `#fbfcfe` |
| Sunken (inputs) | `bg-surface-sunken` | `#0a101d` | `#eef2f7` |
| Border / track | `border-n-800` · `bg-n-800` | `#1a2438` | `#e3e8f0` |
| Text primary | `text-n-0` | white | `#0b1220` |
| Body text | `text-n-300` | `#a3aec3` | `#3b4863` |
| Secondary / labels | `text-n-400` | `#7d8aa3` | `#56627a` |
| Tertiary / dimmed numbers | `text-n-500` | `#5f6b82` | `#6b778f` |
| Accent text & thin bars | `text-accent` · `bg-accent` | `#b8f53d` | `#4d7c0f` |
| Lime fill (tickets, buttons) | `bg-brand-400` + `text-ink` | `#b8f53d` | `#b8f53d` |
| Warn / danger / info text | `text-warn` · `text-danger` · `text-info` | amber-300 / rose-400 / sky-300 | amber-700 / rose-600 / sky-700 |

Values live in `app/globals.css` (`:root` is light, `html.dark` is dark).
Don't name a colour token after a Tailwind utility (`base` would collide with
`text-base`).

**Lime has two jobs.** Bright `brand-400` is for *fills* with `text-ink`
(tickets, buttons, active chips), identical in both themes. Lime used as
*text or a thin bar* is `accent`, which darkens to `#4d7c0f` in light mode,
because bright lime on white is unreadable (1.3:1).

**One accent, one meaning.** Lime means *the pick / value / go*: the favoured
outcome, a confident pick, a value edge, the primary action. Never decorative.
On a lime fill, text is always `text-ink` — **never white on lime**.

**Signal colours** (used sparingly, never as large fills):
- **Amber** — a lean pick (50–64% confidence), premium/crown.
- **Rose** — losses, errors.
- **Sky** — live data (web search, live odds).
- **Violet** — "Upset" picks.

**Confidence ladder** (cards, tickets, stats): ≥65% solid lime · 50–64% outlined
amber · <50% muted. The list can be scanned for strength before reading a
number.

**Contrast.** All text is at least 3:1 in both themes, including dimmed numbers
like the less-likely team's percentage (`text-n-500`, never lighter).

---

## 3. Typography

| Face | Variable | Use |
| --- | --- | --- |
| **Barlow Condensed** 600/700/800 | `font-display` | Page titles, section heads, team names on hero, every "big number" |
| **Inter** | `font-sans` (default) | Body copy, labels, buttons |
| **JetBrains Mono** 500/700 | `font-mono` | Kick-off times, odds, booking codes, source domains |

- Page title: `.display text-5xl sm:text-6xl` (uppercase, tight leading).
- Big numbers: `font-display font-extrabold` + `tnum`, with a smaller `%` sign.
- Small labels above values: `.eyebrow` (11px, uppercase, tracked, zinc-400).
- `.tnum` is **required** on every changing number so digits align.

---

## 4. Iconography

- **One set: [lucide-react](https://lucide.dev).** No mixing icon libraries.
- **Sizes:** 12–14px inline, 15–17px in buttons/nav, 19px mobile bottom nav,
  20px in empty-state tiles. Active nav: stroke `2.4` and lime; rest `2`.
- **Containers:** feature/state icons sit in a `rounded-xl`/`rounded-2xl` tile
  (`bg-brand-400/10 text-brand-400 ring-1 ring-brand-400/20` for features,
  `bg-n-800/70 text-n-400` for empty states).
- **Emoji** only as content (competition flags fallback), never as UI.

---

## 5. Signature components

| Pattern | Where | Notes |
| --- | --- | --- |
| **Scoreboard card** | `PredictionCard`, `SportCard`, `ValueBets` | Header strip (competition eyebrow + mono kick-off) · team rows with big condensed % · 1X2 split bar with the favourite lit · pick ticket |
| **Pick ticket** | card footer, match rail | Confidence ladder styling; `+N% EDGE` tab pinned to the top-right when there's value |
| **Underline tabs** | sport switcher, dashboard | `font-display` uppercase, 2px lime underline on the active tab |
| **Tale of the tape** | match page | Home/away values either side of a label, the better side white with a lime bar |
| **Day groups** | predictions list | "Today", "Tomorrow", "Sat 26 Sep" headings when sorted by kick-off; cards then show time only |
| **State panel** | empty / error / waking | Dashed `.card`, icon tile, condensed title, one-line help, one action |

Component classes (in `globals.css`):

| Class | Use |
| --- | --- |
| `.card` / `.card-interactive` | Surfaces; interactive adds lift, raised bg, brighter border |
| `.chip` + `.chip-active` / `.chip-idle` | Filters — squared `rounded-lg`, lime when active |
| `.btn-primary` / `.btn-secondary` | Lime fill with ink text + glow on hover / bordered surface |
| `.eyebrow` / `.display` | Label and headline type |
| `.skeleton` | Shimmer loading placeholder |
| `.tnum` | Tabular numerals |

**Radii:** `rounded-lg` chips, `rounded-xl` buttons and tickets, `rounded-2xl`
cards. **Shadows:** `shadow-card` → `shadow-card-hover` → `shadow-pop`
(overlays); `shadow-glow` only on the single most important lime element.

---

## 6. Motion, focus & states

- **Hover:** cards lift 2px and brighten; buttons press to `scale(0.98)`.
- **Focus:** a global 2px lime `:focus-visible` ring (keyboard only).
- **Reduced motion** collapses animation/transition durations globally.
- **Loading:** skeletons mirror the real card (same padding, header strip,
  ticket height) so nothing shifts when data arrives.
- **Live dot:** pulsing lime dot for "model live", used once per screen.

---

## 7. Responsible gambling

Keep an **18+** marker and "gamble responsibly" copy in the sidebar, landing
footer and page footers. Predictions are presented as probabilities, never
guarantees.

---

### Handoff checklist

- [ ] No raw hex in components; use `n-*`, `canvas`, `surface`, `accent`, `brand-*`.
- [ ] Checked in **both** light and dark (toggle in the top bar).
- [ ] Nothing white-on-lime; lime fills use `text-ink`.
- [ ] Big numbers use `font-display` + `tnum`; odds/times/codes use `font-mono`.
- [ ] Confidence follows the ladder (lime / amber / muted).
- [ ] Loading states use `.skeleton` in the real layout; empty/error states use the state panel.
- [ ] First prediction visible on a 390×844 screen without scrolling.
- [ ] Icons are lucide; 18+ copy present.
