# Design system & visual tokens

Where every visual setting lives today, how far it sits from the Constructor Fabric
kit, and the plan for keeping all of it in one place.

[← back to the README](../README.md)

## Why this document exists

The visual layer is spread across nine places in two languages. Some of that is
deliberate — the portal renders through both a Python/Jinja path and a React path,
and the chrome has to be byte-identical on both or navigating between routes shows a
visible jump. Some of it is drift. This document records which is which, so a
palette change is a one-file edit instead of an archaeology exercise.

It also records the gap against the Constructor Fabric frontend kit, so the cost of
adopting that kit is a known number rather than a guess.

## Where the visual settings live today

| # | Source | What it defines | Status |
|---|---|---|---|
| 1 | [`frontend/src/styles/base.css:13`](../frontend/src/styles/base.css) | `:root` tokens + element base (button, input, code, headings) | **Live** — React path, via the Vite `<link>` |
| 2 | [`backend/shell.py:327`](../backend/shell.py) `BASE_CSS` | verbatim copy of #1 (its `:root` at line 332) | **Live** — inline `<style>` on every shelled page |
| 3 | [`backend/shell.py:152`](../backend/shell.py) `SHELL_CSS`, `CHART_CSS` | sidebar/chrome + chart surfaces | **Live** |
| 4 | 21 stylesheets in [`frontend/src/styles/`](../frontend/src/styles) | per-page component layout | **Live** |
| 5 | 5 of those add page-local `:root` blocks | `--acc-soft`, `--acc-bg`, `--warn-bg`, `--dup`, `--star`, `--plat`, `--app`, `--c-*` | **Live** — see below |
| 6 | 61 distinct hex literals in CSS (152 occurrences) | one-off colours | **Live** — the main blocker for a dark theme |
| 7 | 17 distinct hex literals in `.ts`/`.tsx` | chart colours passed as recharts props | **Live** |
| 8 | [`backend/server.py:512`](../backend/server.py) `SETUP_HTML` `<style>` | own `:root` with an older GitHub-ish palette | **Shadowed** — `shell.BASE_CSS` is concatenated *after* it at line 538, so BASE_CSS wins for every name both define |
| 9 | [`backend/calibrate.py:40`](../backend/calibrate.py) `_HTML` | own `:root` + full page | **Dead** — `_HTML` is never referenced; `/calibrate` is served by `render_spa_page` ([`server.py:2375`](../backend/server.py)) |

Sources #1 and #2 are an intentional duplicate. The comment at the top of
`base.css` documents the one permitted divergence — `font-display:block` in the
React copy versus `swap` in the Python copy, because on the React MPA every route is
a full page load and `swap` flashed the fallback font on each navigation. Everything
else in those two blocks must stay identical by hand today. That is the duplication
the plan below removes.

Source #8 is worth understanding rather than deleting blind: the local block is not
dead, it styles the setup page's own components (`.step`, `.snum`, `.msg`). Only its
`:root` values are overridden. Its `--line:#d0d7de` never applies — setup borders
actually render as BASE_CSS's `#eceef2`.

### The page-local token additions

Five stylesheets extend the palette in place:

- [`config.css:13`](../frontend/src/styles/config.css) — `--acc-soft`, `--c-other`
- [`identity.css:12`](../frontend/src/styles/identity.css) — `--acc-bg`, `--warn-bg`, `--dup`
- [`calibrate.css:9`](../frontend/src/styles/calibrate.css) — `--star`
- [`semantic_wizard.css:16`](../frontend/src/styles/semantic_wizard.css) — `--acc-soft`
- [`report.css:34`](../frontend/src/styles/report.css) — `--plat`, `--app`, and the 11-colour `--c-*` chart series

About 40 token names exist in total. `--acc-soft` is defined twice with the same
value in two files, which is the failure mode this whole document is about.

## Audit against the Constructor Fabric kit

**Source caveat.** This audit was done against the four kit images. They name every
token (`color/background` … `color/blue`) and give exact px values for typography,
spacing, radii and elevation, but they show **no hex values** for any colour. So the
colour comparison below is structural — naming, coverage, role — plus what is
judgeable by eye. A value-level diff needs the Fabric token JSON or the Figma file.

### Summary

| Layer | Kit | insight-lite today | Verdict |
|---|---|---|---|
| Colour token names | 19 `color/*` semantic | 0 `--color-*`; own 18-name set (`--bg`, `--acc`, `--ink`…) | full rename |
| Token definitions | one source | 2 live copies + 1 shadowed + 1 dead | consolidate |
| Hardcoded colours | none implied | 61 distinct in CSS, 17 in TS/TSX | large delta |
| Typeface (UI) | Inter | Plus Jakarta Sans; Inter is only a fallback | swap |
| Typeface (mono) | JetBrains Mono 11px | `ui-monospace,Menlo` system stack, 19 sites | missing |
| Type scale | 9 named styles | 23 distinct px sizes, incl. 10.5/11.5/12.5/13.5/14.5 | large delta |
| Spacing grid | 7 tokens on a 4px grid | 0 spacing tokens; literals incl. 34px, 38px, 11px | missing |
| Radii | 4 tokens (6/8/12/16) | 16 distinct values | partial |
| Elevation | 1 style; cards use borders only | 2 shadows, on cards *and* every button | conflict |
| Dark theme | full second specimen | absent — no `prefers-color-scheme`, `data-theme` or `color-scheme` anywhere | biggest item |
| shadcn primitives | 13 mappings | none — no tailwind, radix, shadcn or next-themes | not started |
| Icon source | Material Symbols | `lucide-react` + hand-inlined SVG paths | swap |
| Formatting contract | 10 rules | 3 match, 3 conflict, 1 n/a, 3 unverified | partial |
| Accessibility baseline | 7 requirements | 1 met, 4 partial, 2 failing | partial |

### Typography

The kit's nine styles are Inter at 600/500/400 with no stated tracking. This
codebase uses weight **800** for `h1`, 700 for `h2`/`h3`, and negative tracking
(`-.02em`/`-.03em`). Report body text is 14.5px against the kit's 15px. The
half-pixel sizes have no kit equivalent at all — 12.5px alone appears 43 times.

`assets/inter-latin.woff2` is already shipped, so the UI typeface swap is a
`font-family` change plus removing Jakarta, not a new asset. JetBrains Mono would be
a new asset and touches 19 declaration sites.

### Spacing, radii, elevation

No spacing tokens exist. Padding and margin are literals, and several are off a 4px
grid (`24px 34px 64px`, `38px 0 14px`, `8px 11px`).

Radii partly align: `--r:16px` equals `radius/xl` and `--r-sm:12px` equals
`radius/lg`. But buttons and inputs are 10px (17 uses) and there are 11 uses of 7px,
neither of which exists in the kit. There are 46 pill radii (`999px`), mostly
badges — the kit's badge specimens look pill-shaped but its radius table lists no
full/pill token, so that is a gap in the *kit*, not here.

Elevation is a real conflict rather than a value difference. The kit specifies one
shadow (`0 8px 24px`, 28% black) for floating or focused surfaces and states that
standard cards rely on borders. This codebase puts `--sh` on every card **and** every
button, `--sh-lift` on hover, and a coloured glow on primary buttons
(`0 4px 12px rgba(91,91,240,.28)`). Adopting the kit means removing card and button
shadows, which is visible on every page.

### One collision to plan for

`#8b5cf6` — visually the kit's `color/primary` purple — already appears 7 times here,
but as a *chart* colour (`--app`, `--c-loc`, `--c-epic`). The primary action colour is
indigo `#5b5bf0`. Renaming toward the kit collides those two roles unless the chart
series is separated from the action palette first.

### Data formatting contract

| Rule | Kit | Here | |
|---|---|---|---|
| Integers | `5,551` | `fmtNum` → `toLocaleString("en-US")` | match |
| Percentages | `44.7%`, decimal only when needed | `fmtPct` strips a trailing `.0` | match |
| Change | arrow + value, never colour alone | `▲ 20%` / `▼` / `±` | match in semantics; glyph differs (`▲` vs `↑`) |
| Compact numbers | `3.38M`, 2 significant decimals | `fmtLoc` → `3.49M`, but `25.7K` (1 decimal) | kit is silent on the K form |
| **Durations** | `4.9 d` — space before the unit | `_hours`/`_dur` → `3.2d`, `18h`, `2m28s`, `1h03m` | **conflict** — no space, plus composite forms the kit has no rule for |
| **Dates** | `17 Jul 2026` | `toLocaleDateString(undefined, …)` → `Jul 17, 2026` under en-US | **conflict** |
| **Date & time** | `17 Jul 2026, 09:13 UTC` | `%Y-%m-%d %H:%M:%S UTC` | **conflict** |
| Currency | `$18.40` | no currency rendering in this product | n/a |
| Time, Ranges | `09:13 UTC`, `1 May–17 Jul 2026` | no confirmed user-facing site | unverified |

Two things beyond the kit comparison are worth fixing on their own merits. The
`undefined` locale argument at [`FilterBar.tsx:44`](../frontend/src/components/FilterBar.tsx)
makes the rendered date depend on the viewer's browser locale, which is both a
contract violation and a live hazard for the pixelmatch visual tests. And durations
have the widest blast radius of any formatting change, because cycle time appears on
most pages.

### Accessibility baseline

Contrast ratios, computed from the actual token values:

| Foreground | On | Ratio | AA normal (4.5) |
|---|---|---|---|
| `--mut #8a93a3` | `--panel #ffffff` | 3.10 | fail |
| `--mut #8a93a3` | `--bg #f5f6f9` | 2.86 | fail (also fails large text) |
| `--good #0f9d58` | `#ffffff` | 3.51 | fail |
| `--warn #b7791f` | `#ffffff` | 3.64 | fail |
| `--bad #e5484d` | `#ffffff` | 3.91 | fail |
| `--ink2 #475467` | `#ffffff` | 7.69 | pass |
| `--ink #101828` | `#ffffff` | 17.75 | pass |
| `--acc #5b5bf0` | `#ffffff` | 5.00 | pass |
| `#ffffff` | `--acc #5b5bf0` | 5.00 | pass |

`--mut` is the most-used token in the codebase (223 references) and fails AA
everywhere, worst against the page background. The four status colours fail as text
but pass for large text and non-text use.

The rest of the baseline:

- **Focus ring** — two `:focus-visible` rules exist, both for specific report chips
  ([`report.css:232`](../frontend/src/styles/report.css)). Buttons have no focus style
  at all; inputs use `:focus`, not `:focus-visible`.
- **44×44 touch target** — buttons are `padding:8px 14px` at `13.5px/1.4`, about
  **35px** tall.
- **Keyboard access** — 25 `onClick` handlers sit on `div`/`span`/`td`/`li` across 6
  pages, with 4 `onKeyDown` and no `tabIndex` in the app. Those controls are
  mouse-only.
- **Status not by colour alone** — met: delta chips carry `▲`/`▼` and badges carry
  text.
- **Reduced motion** — honoured in three places (loadbar, skeleton,
  [`Loading.tsx`](../frontend/src/components/Loading.tsx)); recharts animation is not
  gated.
- **Names for icon-only actions** — 34 `aria-label`s against roughly 22 icon-only
  buttons. Partial; needs a per-site pass.

### Components

Hand-rolled and present: button, input/select/textarea, tabs
([`SegBar.tsx`](../frontend/src/components/SegBar.tsx)), table
([`DataTable.tsx`](../frontend/src/components/DataTable.tsx)), skeleton, KPI card,
progress bars, chart legend, chips, and an ad-hoc `.dov`/`.dbox` modal overlay with
no focus trap.

Absent: dialog, sheet/side panel, toast, alert, popover, pagination, breadcrumb, and
styled checkbox/radio/switch — two files use a bare `type="checkbox"`, and there are
no radios.

The kit's entire right-hand column (Button, Input, Select, DropdownMenu, Checkbox,
RadioGroup, Switch, Tabs, ToggleGroup, Tooltip, Popover, Alert, Sonner, Dialog,
Sheet, Table, Pagination, Skeleton, next-themes) is unimplemented. This frontend has
four runtime dependencies — `react`, `react-dom`, `recharts`, `lucide-react` — and
hand-written CSS. Adopting that column means adding tailwind, radix, shadcn and
next-themes, which is a separate decision from adopting the tokens, and is where most
of the cost sits.

## Two constraints that shape any change here

**Dual render paths.** Numbers render through both `render.py`'s Jinja filters and
[`frontend/src/lib/format.ts`](../frontend/src/lib/format.ts), which is documented as
needing to stay byte-identical. Every formatting change is two implementations plus
the macros in `templates/panels/`.

**The pixel gate.** `npm run visual:baseline` / `visual:candidate` / `visual:diff`
compares screenshots with pixelmatch. Any token change means regenerating the
baseline wholesale — so the regression net is unavailable for exactly the change that
most needs it. Sequence around this: land behavioural fixes first, under the existing
baseline, then take the visual change as one deliberate re-baseline.

## Plan: one place for the visual settings

The goal is that a palette, radius, or type-scale change is one edit in one file, and
that a second theme is possible at all.

### Single source

Add `design/tokens.json` as the only hand-edited definition of colour, spacing,
radius, type-scale and elevation, with a light and a dark set. A small generator
emits three artefacts, all committed, none hand-edited:

| Generated file | Consumed by | Replaces |
|---|---|---|
| `frontend/src/styles/tokens.css` | Vite, imported by every entry | the `:root` block in `base.css` |
| `backend/tokens.py` (a `TOKENS_CSS` string) | `shell.py`, injected where `BASE_CSS` is today | the hand-copied `:root` in `BASE_CSS` |
| `frontend/src/lib/tokens.ts` | recharts props, `KpiTile` | the 17 hex literals in `.ts`/`.tsx` |

JSON-as-source rather than CSS-as-source specifically because of the third row:
chart colours are passed to recharts as JS values, and CSS custom properties cannot
reach them. One generator covers all three languages. It also matches the pattern
already in use in `dzarlax/design-system` (`tokens/tokens.json` → `bin/gen-tokens.py`
→ `dist/`), so the workflow is not new.

A lighter alternative, if the generator feels like too much machinery: make
`tokens.css` the single hand-edited file and have `shell.py` read it from disk at
import time. The Dockerfile's `COPY . .` ships `frontend/src`, so the file is present
at runtime. This removes the #1/#2 duplication for free but leaves the TS chart
colours unsolved.

### Two tests to add with it

- **Drift test** — regenerate into a temp dir and diff against the committed
  artefacts; fail if they differ. This is the same guarantee the codebase already
  asks for by hand in the `format.ts` and `BASE_CSS` comments, made mechanical.
- **Contrast test** — assert every foreground/background token pair in the JSON meets
  WCAG 2.2 AA for its intended use. The table above is what this test would have
  caught years ago.

### Order of work

1. **Delete** the dead `_HTML` block in `calibrate.py`. No behaviour change.
2. **Fold** the setup page's shadowed `:root` into its component rules, so
   `SETUP_HTML` no longer appears to define a palette it does not define.
3. **Introduce** `design/tokens.json` + generator, emitting exactly today's values.
   Nothing changes visually; the pixel baseline still passes. This is the step that
   makes everything after it cheap.
4. **Lift** the page-local `:root` additions and the 61 CSS hex literals into the
   JSON, separating the `--c-*` chart series from the action palette while doing it
   (this is what unblocks both the dark theme and the `#8b5cf6` role collision).
5. **Then** the visual decisions become one-file edits: fix `--mut` and the status
   colours to pass AA; collapse the radius set; add the dark palette.

Steps 1–4 carry no design decision and no visual change. They are worth doing
whether or not the Fabric kit is adopted.

### Fixes that do not need any of the above

- Add a global `:focus-visible` ring.
- Make the 25 `div`-with-`onClick` sites keyboard-reachable.
- Pin the locale at `FilterBar.tsx:44`.
- Gate recharts animation on `prefers-reduced-motion`.

### If the Fabric kit is adopted

Two distinct scopes, worth pricing separately:

- **Tokens only** — rename to `--color-*`, add `--space-*`, collapse the radii, add
  the dark palette, keep the hand-written CSS. Contained: 21 stylesheets plus the
  generated artefacts. Feasible after step 4 above.
- **Tokens plus shadcn** — introduces tailwind, radix, shadcn and next-themes into a
  four-dependency frontend, and rewrites every control. This is a frontend rewrite,
  not a restyle.

The formatting contract is a third, independent axis: it lives in `render.py` and
`format.ts` and has the same duplication problem as the tokens, with the same fix
shape (one spec, generated or drift-tested on both sides).
