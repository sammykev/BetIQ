# BetIQ — Design System & Style Guide

The reference for keeping the UI feeling *designed rather than assembled*. Everything
here already lives in the codebase — this document is the handoff layer that makes it
reusable. Tokens are defined in `tailwind.config.js` and `app/globals.css`; reach for
the named token, never a raw hex value in a component.

---

## 1. Grid & spacing

Layout is built on Tailwind's 4px spacing scale. Consistent alignment is the fastest
way to make a screen feel intentional, so stick to these rungs and avoid arbitrary
values (`p-[13px]`).

| Purpose | Token | Value |
| --- | --- | --- |
| Page gutters | `px-4 sm:px-6` | 16 → 24px |
| Max content width | `max-w-6xl mx-auto` | 1152px, centered |
| Section rhythm | `space-y-6` | 24px between blocks |
| Card grids | `gap-4` | 16px |
| In-card stacks | `gap-3` / `gap-3.5` | 12–14px |
| Inline label ↔ value | `gap-1.5` / `gap-2` | 6–8px |

**Grid.** Card collections use one responsive grid across every page:
`grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4`. The desktop shell reserves a
fixed `lg:pl-60` sidebar rail; the top bar and main content both inherit that offset so
nothing drifts out of alignment.

**Checking a page against the grid:** every left edge inside the content column should
line up on the `max-w-6xl` container; every card in a row shares one height (cards flex
their footer with `mt-auto`); vertical gaps between sibling blocks are a single
`space-y-*` value, not hand-tuned margins.

---

## 2. Color

Semantic tokens live in `:root` (light) and a `prefers-color-scheme: dark` override,
mirrored by the `brand` / `surface` scales in Tailwind.

| Role | Light | Dark | Token |
| --- | --- | --- | --- |
| Background | `#fafafa` | `#09090b` | `--bg` |
| Surface (cards) | `#ffffff` | `#101014` | `--surface` / `.card` |
| Border | `#e4e4e7` | `#26262b` | `--border` |
| Text primary | `#0f172a` | `#f4f4f5` | `--text-primary` |
| Text secondary | `#64748b` | `#a1a1aa` | `--text-secondary` |
| Accent (brand) | `#059669` | `#10b981` | `--accent` / `brand-600` |

**Hierarchy.** Three text weights carry the whole UI: primary (`zinc-900`/`white`) for
values and headings, secondary (`zinc-500`) for supporting copy, tertiary
(`zinc-400`) for metadata and labels. If everything is bold, nothing is — reserve
`font-black` for numbers and page titles.

**Accent discipline — the "unfinished" mistake.** Brand green means *one* thing:
the primary action or a positive signal (bankers, value edges, confidence ≥ 65%). It is
never a decorative fill. Secondary sports/markets borrow a fixed tint set —
sky, violet, amber, rose, teal — always at `50`/`500-10%` background with a `600` icon,
never as large color blocks. The most common way a site reads as unfinished is a stray
default accent (e.g. the browser's blue focus ring on a green product); we override that
globally — see §5.

---

## 3. Iconography

- **One set: [lucide-react](https://lucide.dev).** Every functional icon is a lucide
  glyph — do not mix in Font Awesome, Heroicons, or default OS emoji for UI affordances.
- **Sizing.** `14px` inline with text, `16–17px` for nav and buttons, `19px` for
  mobile bottom-nav. Stroke width is `2` at rest, `2.4` for the active nav item — the
  weight bump reads as "selected" without a color change alone.
- **Container.** Feature/stat icons sit in a `w-9 h-9` (or `w-10 h-10`) `rounded-xl`
  tinted chip, icon centered — this keeps icons optically aligned regardless of their
  individual glyph proportions.
- **Emoji exception.** Sport identifiers (⚽ 🏀 🎾 🏓) and playful empty/waking states
  use emoji deliberately as *content/branding*, not as UI controls. Keep that line
  crisp: an emoji never stands in for a button icon.

---

## 4. Loading states

Where they matter, and where they don't:

- **Use a skeleton** for the predictions/sport card grids — the layout is known ahead of
  time, so mirror it. Skeletons use the `.skeleton` utility (a soft left-to-right
  **shimmer**, not a flat pulse) so waiting content reads as *premium*, not *broken*.
- **Use a spinner** only for indeterminate, full-screen gates (auth bootstrap).
- **Use inline motion** for actions in progress — the refresh icon spins
  (`animate-spin`) while the request is out.
- **Avoid the flash of blank content:** skeleton cards match the real card's padding,
  radius, and internal rhythm, so the swap to real data doesn't shift layout.
- **Don't** skeleton trivially fast or tiny UI (a toggle, a chip) — it's noise.

---

## 5. Cursor, hover & focus

Three subtle treatments, no gimmicks:

1. **Card lift** (`.card-interactive`): on hover, `-translate-y-0.5` + `shadow-card-hover`
   + a slightly darker border; `active:` returns it to rest. Motion is
   `duration-200` — perceptible, never sluggish.
2. **Button press** (`.btn-primary` / `.btn-secondary`): `active:scale-[0.98]` gives a
   tactile "click" with near-zero performance cost.
3. **Chips & nav**: color/border transitions only (`transition-all`), no transform — a
   dense row of moving chips would feel gimmicky.

**Focus (accessibility + polish).** A single brand-tinted ring
(`:focus-visible { outline: 2px solid var(--accent) }`, offset `2px`) is applied globally
in `globals.css` and only shows for keyboard users, never on mouse click.

**Reduced motion.** `@media (prefers-reduced-motion: reduce)` collapses all decorative
animation and transition durations. Respect the OS preference rather than overriding it.

**Custom cursors** are intentionally *not* used — on a data/betting product they distract
from scanning numbers and cost paint performance for no clarity gain.

---

## 6. Component vocabulary

Prefer these composed classes (defined in `globals.css`) over re-declaring utilities:

| Class | Use |
| --- | --- |
| `.card` | Any elevated surface (white/zinc-900, `rounded-2xl`, `shadow-card`). |
| `.card-interactive` | A `.card` that is clickable — adds hover lift + press. |
| `.chip` + `.chip-active` / `.chip-idle` | Filter/selector pills. |
| `.btn-primary` / `.btn-secondary` | Actions. Primary = brand fill; secondary = bordered. |
| `.skeleton` | Shimmer loading placeholder. |
| `.tnum` | Tabular numerals — **required** on every odds/percentage/stat so digits align. |
| `.text-gradient` | Brand gradient text, reserved for hero emphasis only. |

**Radii:** `rounded-xl` (12px) for controls, `rounded-2xl` (16px) for cards,
`rounded-full` for chips, badges, and avatars.

**Shadows:** `shadow-card` at rest → `shadow-card-hover` on interaction → `shadow-pop`
for modals/overlays. Elevation only ever increases toward the user's focus.

---

### Handoff checklist

- [ ] No raw hex in components — use `brand-*`, `zinc-*`, or a semantic token.
- [ ] Numbers carry `.tnum`.
- [ ] Interactive surfaces use `.card-interactive` / `.btn-*`, not ad-hoc hover styles.
- [ ] Loading grids use `.skeleton` mirroring the real layout.
- [ ] Icons are lucide, sized per §3; emoji only as content.
- [ ] Both light and dark verified.
