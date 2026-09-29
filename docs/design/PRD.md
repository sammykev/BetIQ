# BetIQ redesign — product & design brief

Status: shipped (phases 1–4) · Owner: BetIQ · Skills: `better-ui`, `better-typography`,
`better-colors`, `better-layout`, `better-accessibility`, `better-writing`,
`shadcn` (installed in `.claude/skills`)

## 1. Product

BetIQ turns years of results into honest probabilities for football,
basketball, tennis and table tennis, and turns picks into SportyBet and
football.com booking codes. People come to it on a phone, between other
things, to answer three questions fast:

1. **What should I back today?** Predictions, confidence, best line.
2. **Is it happening?** Live scores, how our picks are doing.
3. **Can I trust it?** The track record, graded against every final.

Every screen serves one of those. Anything that doesn't is removed.

### Users
- Nigerian sports bettors, mobile first (390×844 is the reference screen),
  on patchy data; many book on SportyBet or football.com.
- Returning daily: the daily slips, the date strip, live scores.

### Jobs, by page
| Page | Job | Primary action |
| --- | --- | --- |
| Home (all sports) | Today's picks, live & finished, past days graded | Open a match, add a pick to the slip |
| Match | Everything about one match: chances, markets, form, H2H | Add a line to the slip |
| Optimizer | A slip for the odds I want | Get a booking code |
| Daily slips | Five ready slips with codes | Copy / open a code |
| History | Are the chances honest? | Switch sport / period |
| Dashboard | My codes and how they did | Open a ticket |
| Bet slip (drawer) | Book my picks | Get a SportyBet / football.com code |

## 2. Brand identity — a mix of the old and the new

**Kept (recognition):** the BetIQ name and mark; dark-first navy surfaces
with light mode; **one electric-lime accent** that means "pick / value /
go"; the condensed scoreboard face for numbers that must read at a glance
(scores, odds, percentages, codes).

**New (refresh):**
- **Calmer voice.** Sentence-case headings in the body face instead of
  all-caps condensed shouting; condensed caps stay only on scoreboard
  numbers and tiny eyebrows.
- **Depth from light, not lines.** Cards and controls lose their 1px
  borders for layered shadow rings; borders remain only as dividers and
  input outlines.
- **A tuned neutral ramp** (navy-tinted, perceptually even) with separate
  semantic tokens for text, surfaces and borders.
- **Motion with purpose.** Tactile presses, a sliding tab indicator, soft
  modal and drawer entrances, a staggered first paint, live scores that
  tick. Nothing loops except "live".

Personality: *confident, precise, calm.* A trading screen, not a casino.

## 3. Design principles (from the installed skills)

**Colour** (`better-colors`)
- One neutral ramp, one accent ramp (lime), status only where rendered:
  won (lime), lost (rose), warn (amber), info (sky). Won/lost always carry
  an icon or word too, never colour alone.
- Components use role tokens only (`text-n-0`, `bg-surface`, `accent`…),
  never primitives or raw hex.
- One filled lime action per view.

**Typography** (`better-typography`)
- Inter for UI and headings (weights 400/500/600/700), Barlow Condensed
  for scoreboard numbers and eyebrows, JetBrains Mono for codes.
- Scale: 12 / 13 / 14 / 16 / 18 / 22 / 28 / 36. Headings line-height 1.1
  with −0.02em tracking; body 1.5; eyebrows +0.08em.
- `tabular-nums` on every changing number; `text-wrap: balance` on
  headings, `pretty` on descriptions; inputs 16px on mobile.

**Surfaces & motion** (`better-ui`, exact values)
- Concentric radii: card 20px → inner 12px with 8px padding.
- Shadow-as-border rings: light `0 0 0 1px oklch(0 0 0 / .06)` + lift;
  dark `0 0 0 1px oklch(1 0 0 / .08)`, hover `/ .13`.
- Press: `scale(0.96)`. Easing `cubic-bezier(0.2, 0, 0, 1)`. High-frequency
  feedback ≤150ms on colour/opacity. Name transitioned properties; never
  `transition: all`.
- Enter: opacity + 12px translateY + 4px blur, staggered 100ms, first
  paint only. Exit: softer, small translateY, ease-out.
- Icon swaps: scale .25→1, opacity 0→1, blur 4px→0 (spring 0.3s, bounce 0).
- Theme switch suppresses transitions for one frame.
- `prefers-reduced-motion`: no movement, colour changes only.

**Layout & accessibility** (`better-layout`, `better-accessibility`)
- Max width 1152px, gutters 16→24px, one responsive card grid.
- Hit areas ≥ 40px on touch. Visible focus ring everywhere (lime, 2px).
- Dialogs trap focus, close on Escape, return focus; drawers the same.
- Nothing clipped at 320px or 200% zoom.

## 4. Component system

Built in the project's stack (Next 14, Tailwind 3, lucide icons) with
shadcn-style primitives in `components/ui`, variants via
`class-variance-authority`, merged classes via `cn()`, motion via `motion`:

`Button` (primary · secondary · ghost · subtle; sm/md/lg/icon; press scale)
· `Card` · `Badge` · `Tabs` (sliding indicator) · `Dialog` and `Sheet`
(animated, focus-trapped) · `Skeleton` · `Stat` · `Empty` · `Chip`.

Existing class names (`.card`, `.chip`, `.btn-primary`, `.eyebrow`,
`.skeleton`) are re-skinned in `globals.css`, so every screen moves to the
new look at once; pages then adopt the primitives.

## 5. Scope & phases

1. **Foundation:** tokens, type, surfaces, motion, primitives; global
   classes re-skinned.
2. **Shell & home:** sidebar, top bar, mobile nav, date strip, match
   cards, live lists, sport tabs, match modals, bet slip drawer.
3. **Flows:** optimizer, daily slips, history, dashboard, match page.
4. **Landing & admin** (admin: tokens only).

Each phase ships on its own and is checked in both themes at 390px and
1280px.

## 6. Success

- The first prediction is visible without scrolling at 390×844.
- No interactive control without an accessible name or visible focus.
- Every animation has a static cue and honours reduced motion.
- No page regresses in content or function.
