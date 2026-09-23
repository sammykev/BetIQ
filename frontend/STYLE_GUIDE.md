# BetIQ — "Matchday" Design System & Style Guide

A dark, sportsbook-grade identity: near-black navy surfaces, one electric-lime
accent, and condensed scoreboard type for anything that should read at a
glance. Everything below already lives in the code — tokens in
`tailwind.config.js` and `app/globals.css`, fonts in `app/layout.tsx`. Reach for
the named token or component class, never a raw hex value in a component.

**Matchday is dark-only.** `<html class="dark">` is set permanently, so every
`dark:` variant always applies. New components don't need `dark:` prefixes;
write the dark styles directly.

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

| Role | Value | Token |
| --- | --- | --- |
| Page background | `#070b14` | `bg-ink` · `--bg` |
| Card surface | `#0f1729` | `bg-surface` · `.card` |
| Raised (hover, slip) | `#131d33` | `bg-surface-raised` |
| Sunken (inputs) | `#0a101d` | `bg-surface-sunken` |
| Border | `#1a2438` | `border-zinc-800` |
| Text primary | `#e3e8f0` / white | `text-zinc-100` / `text-white` |
| Text secondary | `#7d8aa3` | `text-zinc-400` |
| Text tertiary | `#5f6b82` | `text-zinc-500` |
| Accent | `#b8f53d` | `brand-400` · `--accent` |

`zinc-*` is re-tinted toward navy in `tailwind.config.js`, so existing zinc
utilities land on Matchday surfaces automatically.

**One accent, one meaning.** Lime means *the pick / value / go*: the favoured
outcome, a confident pick, a value edge, the primary action. Never decorative.
On a lime fill, text is always `text-ink` — **never white on lime**.

**Signal colours** (used sparingly, never as large fills):
- **Amber** — a lean pick (50–64% confidence), premium/crown.
- **Rose** — losses, errors.
- **Sky** — live data (web search, live odds).
- **Violet** — "Upset" picks.

**Confidence ladder** (cards, tickets, stats): ≥65% solid lime · 50–64% outlined
amber · <50% muted zinc. The list can be scanned for strength before reading a
number.

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
  `bg-zinc-800/70 text-zinc-400` for empty states).
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

- [ ] No raw hex in components; use `ink`, `surface`, `zinc-*`, `brand-*`.
- [ ] Nothing white-on-lime; lime fills use `text-ink`.
- [ ] Big numbers use `font-display` + `tnum`; odds/times/codes use `font-mono`.
- [ ] Confidence follows the ladder (lime / amber / muted).
- [ ] Loading states use `.skeleton` in the real layout; empty/error states use the state panel.
- [ ] First prediction visible on a 390×844 screen without scrolling.
- [ ] Icons are lucide; 18+ copy present.
