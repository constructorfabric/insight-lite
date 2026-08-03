# Design system & visual tokens

Where every visual setting lives today, how far it sits from the Constructor Fabric
kit, and the plan for keeping all of it in one place.

[← back to the README](../README.md)

## Why this document exists

The visual layer was spread across nine places in two languages. Some of that is
deliberate — the portal renders through both a Python/Jinja path and a React path,
and the chrome has to be byte-identical on both or navigating between routes shows a
visible jump. Some of it was drift. This document records which is which, so a
palette change is a one-file edit instead of an archaeology exercise.

The shared base is now single-source (rows 1–2a below, and *Plan* further down). The
page-level spread — rows 4–7 — is still real and is the next step.

It also records the gap against the Constructor Fabric frontend kit, so the cost of
adopting that kit is a known number rather than a guess.

## Where the visual settings live today

| # | Source | What it defines | Status |
|---|---|---|---|
| 1 | [`design/tokens.json`](../design/tokens.json) + [`base-elements.css`](../frontend/src/styles/base-elements.css) | every token value, and the element base | **Live, and the only hand-edited source** — see the plan below |
| 2 | `frontend/src/styles/tokens.css`, [`backend/tokens.py`](../backend/tokens.py) | the same, per path | **Generated** from #1 — do not edit |
| 2a | [`base.css`](../frontend/src/styles/base.css), [`shell.py`](../backend/shell.py) `_FONTS_CSS` | the `@font-face` pair only | **Live** — the one thing the paths must *not* share (`font-display` block vs swap) |
| 3 | [`backend/shell.py:152`](../backend/shell.py) `SHELL_CSS`, `CHART_CSS` | sidebar/chrome + chart surfaces | **Live** |
| 4 | 21 stylesheets in [`frontend/src/styles/`](../frontend/src/styles) | per-page component layout | **Live** — layout only; they hold no colour values now |
| 5 | ~~7 page-local `:root` blocks~~ | `--acc-soft`, `--acc-bg`, `--warn-bg`, `--dup`, `--star`, `--plat`, `--app`, `--violet`, `--code-bg`, `--c-*` | **Gone** — all moved into `design/tokens.json` |
| 6 | ~~51 distinct hex literals in CSS~~ | one-off colours | **Gone** — 0 remain outside the generated `tokens.css` |
| 7 | ~~11 colour literals in `.ts`/`.tsx`~~ | inline styles, score bands, palettes | **Gone** — now `frontend/src/lib/tokens.ts` |
| 7a | ~~68 distinct colour literals in `backend/*.py`~~ | `_ELEM_PALETTE`, `_PILLAR_COLORS`, `_WORKTYPE_COLORS`, `_BAND_COLORS`, chart series colours, element defaults | **Gone** — they read `backend/tokens.py`'s `VALUES` and palettes, generated from the same source |
| 8 | ~~`backend/server.py` `SETUP_HTML`~~ | a whole pre-React page, own `:root` with an older GitHub-ish palette | **Removed** — it was dead; `/setup` is `render_spa_page("setup", …)` and its styles live in [`setup.css`](../frontend/src/styles/setup.css) |
| 9 | ~~`backend/calibrate.py` `_HTML`~~ | own `:root` + a full page | **Removed** — it was dead code, never referenced; `/calibrate` is served by `render_spa_page` ([`server.py:2375`](../backend/server.py)) |

Rows 1–2a used to be one row each of hand-copied CSS: `base.css` carried the token
block and the element base for the React path, and `shell.BASE_CSS` carried a
hand-kept copy of both for the Python path, reconciled by eye. They are now generated
from one source, and a test asserts the two paths cannot disagree.

Sources #8 and #9 were the same shape: complete pages left behind by the React
migration, each carrying its own competing `:root`. Both are gone. The reasoning that
lived in their comments was not thrown away — `setup.css` and `SetupWizard.tsx` still
record what was ported and which rules were deliberately omitted, now pointing at git
history rather than a live symbol.

Still outstanding from the same era: the unused `send_html_file_with_nav` method on
the request handler.

### Why the backend has colours at all

This is the part that surprises people reading the code, so it is worth stating
plainly: the server attaches a colour **to the data**, not to the page.

A stylesheet can say what colour a *class* is. It cannot say what colour *Acme Corp*
is, because the set of companies comes out of the database at request time — and the
same is true of product elements, work types, board stages and score bands. So the
payload for a chart carries `{"name": "Acme Corp", "vals": [...], "color": "#8250df"}`
and the client draws what it is told.

Company colours are the clearest case, and the reason they are computed rather than
listed: `store.company_color_map` derives each one from the company **name**, so a
company keeps its colour when ranks change, when another company is added, and between
one report and the next. That behaviour is pinned by
[`tests/test_company_colors.py`](../tests/test_company_colors.py), which exists because
the first version handed colours out in descending-commit order — two companies
swapping places swapped colours, in a chart people read week over week.

What the backend emits, all of it now from `design/tokens.json` via
[`backend/tokens.py`](../backend/tokens.py):

| Palette | Assigned by | Drives |
|---|---|---|
| `COMPANY_PALETTE` + `company-empty` | name hash, or a pin from Manage → Config | company series in contribution and trend charts |
| `CATEGORY_SWATCHES` | index | product elements, user-defined work types |
| `ELEMENT_DEFAULTS` | fixed | the built-in `platform` / `app` elements |
| `WORKTYPE_COLORS` | key | conventional-commit type rollup (feat/fix/docs/…) |
| `FLOW_STAGE_COLORS` | key | board stages (backlog → released) |
| `BAND_COLORS`, `PILLAR_COLORS`, `score-*` | key / threshold | developer-score bands, pillars, cell shading |
| `SERIES_COLORS` | key | named series — Opened, Merged, Median TTM, Contributors, rework |

Before this, those were 68 literals in `render.py`, `store.py`, `collect.py` and
`semantic_metrics.py`. Three of them — `_ELEM_PALETTE`, `store._PAL`, `store._ppal` —
were the *same eight-colour list copied three times*; counting the design source and
`lib/tokens.ts`, that one palette existed five times across three languages.
`_PILLAR_COLORS` was a byte-identical twin of the TS `PCOLOR` map, and `Trend.tsx`'s
legend swatches were hand-matched to the series colours `render.py` emits — two files
that had to change together with nothing saying so.

### The page-local token additions

Seven stylesheets extend the palette in place:

- [`config.css:13`](../frontend/src/styles/config.css) — `--acc-soft`, `--c-other`
- [`identity.css:12`](../frontend/src/styles/identity.css) — `--acc-bg`, `--warn-bg`, `--dup`
- [`calibrate.css:9`](../frontend/src/styles/calibrate.css) — `--star`
- [`metrics.css:8`](../frontend/src/styles/metrics.css) — `--acc-soft`, `--violet`, `--violet-bg`, `--code-bg`, `--code-fg`
- [`whatsnew.css:20`](../frontend/src/styles/whatsnew.css) — `--app`
- [`semantic_wizard.css:16`](../frontend/src/styles/semantic_wizard.css) — `--acc-soft` + an 8-colour `--c-*` set
- [`report.css:34`](../frontend/src/styles/report.css) — `--plat`, `--app`, and an 11-colour `--c-*` chart series

39 distinct token names exist in total, and two of them mean different things
depending on which page you are on:

| Token | Value | Where |
|---|---|---|
| `--app` | `#8b5cf6` | `report.css` |
| `--app` | `#8250df` | `whatsnew.css` |
| `--c-epic` | `#8b5cf6` | `report.css` |
| `--c-epic` | `#5b5bf0` | `semantic_wizard.css` |

Neither collides at runtime — each page loads only its own sheet — but a token whose
value depends on the route is not a token, and consolidating the two sets in step 4
has to resolve them rather than pick one silently. `--acc-soft` is defined three times
with the same value, and the same purple appears under three names (`--app`,
`--violet`, `--c-loc`), which is the failure mode this whole document is about.

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

**The chrome is server-rendered, the content is React.** This is not two competing page
renderers — the React migration (`docs/superpowers/specs/2026-07-22-react-frontend-migration-design.md`)
finished, and every route now goes through `render_spa_page`. What Python still renders
is the shell: `<head>`, the inline `<style>`, and the sidebar markup, deliberately, so
navigation is correct before any JS runs and mounting React over it moves nothing. That
is the whole reason the Python side needs the visual base *as a string*.

Jinja survives in exactly one place: `templates/panels/01_helpers.j2`, rendered by
`render_panel_macro` for the dashboard panels' server-side preview (two call sites in
`dashboards.py`). It uses the `|num`/`|pct` filters, so the byte-identical requirement
between `render.py`'s filters and
[`frontend/src/lib/format.ts`](../frontend/src/lib/format.ts) is real but narrow — it
binds those panels, not every number on every page.

**The pixel gate.** `npm run visual:baseline` / `visual:candidate` / `visual:diff`
compares screenshots with pixelmatch. Any token change means regenerating the
baseline wholesale — so the regression net is unavailable for exactly the change that
most needs it. Sequence around this: land behavioural fixes first, under the existing
baseline, then take the visual change as one deliberate re-baseline.

## Plan: one place for the visual settings

The goal is that a palette, radius, or type-scale change is one edit in one file, and
that a second theme is possible at all.

### Single source — how it is wired

Two hand-edited files hold the shared visual base. Nothing else is written by hand,
and neither render path has a copy of its own.

| Hand-edited | What it holds |
|---|---|
| [`design/tokens.json`](../design/tokens.json) | every token value — colour, radius, shadow — plus which colours are used as body text on which surfaces |
| [`frontend/src/styles/base-elements.css`](../frontend/src/styles/base-elements.css) | the element base: `html`/`body` reset, headings, buttons, form controls, `code` |

[`tools/gen_tokens.py`](../tools/gen_tokens.py) turns those into two generated
artefacts, both committed, neither to be edited:

| Generated | Consumed by |
|---|---|
| `frontend/src/styles/tokens.css` | `base.css`, via `@import`; Vite inlines it into every entry's bundle |
| `backend/tokens.py` — `TOKENS_CSS` + `ELEMENTS_CSS` | `shell.py`, which concatenates both into `BASE_CSS` |

    python3 tools/gen_tokens.py            # write the artefacts
    python3 tools/gen_tokens.py --check    # exit 1 if they are stale

**Why the Python side carries CSS at all.** It is not design-in-Python: `backend/tokens.py`
holds no decisions, only generated text. The server-rendered pages inline their CSS
into a `<style>` block rather than linking a stylesheet, because that is what makes the
chrome correct before any JS runs (see `render_spa_page`) — so the Python path needs the
CSS *as a string*. Two alternatives exist if that still grates: have `shell.py` read the
two CSS files off disk at import time (the Dockerfile's `COPY . .` ships them, and this
removes the generated Python entirely, at the cost of a runtime file read), or serve them
as static `/assets/` files and `<link>` them (removes CSS from Python completely, but
adds a blocking request per page, a cache-busting scheme, and a FOUC risk on the
server-rendered chrome).

**What stays deliberately un-shared.** The `@font-face` pair. `font-display` is `block`
on the React path and `swap` on the Python path, and that divergence is load-bearing —
so `@font-face` lives in `base.css` and `shell._FONTS_CSS` separately, and a test fails
if either shared file introduces one.

`themes` in the JSON is a map, so adding a `dark` key emits its block on both paths with
no generator change. It has one entry today.

Still to come: `frontend/src/lib/tokens.ts` for the chart colours passed to recharts as
JS values. That is the reason the source is JSON rather than CSS — custom properties
cannot reach a JS prop — and it lands with step 4, when the `--c-*` series moves in.

### The tests that hold it together

[`tests/test_design_tokens.py`](../tests/test_design_tokens.py):

- **Drift** — `gen_tokens.py --check`; fails if a generated file was hand-edited or the
  generator was not re-run.
- **Parity** — the two paths' assembled CSS is rule-for-rule identical apart from
  `@font-face`, and `shell.py` defines no tokens of its own. This is the reconciliation
  the `base.css` comment used to ask a human to do by eye.
- **Import order** — a rule added above the `@import`s in `base.css` would make CSS
  silently drop the tokens and the element base. Verified to fail when violated.
- **Contrast** — every body-text pair declared in the JSON against WCAG 2.2 AA, with
  today's eight failures pinned at their measured ratios so a *new* one fails the build
  and a fixed one is a deleted line.
- **No stray colours** — no colour literal in any stylesheet, component, or backend
  module outside the generated files. Read its docstring before trusting it: two
  earlier versions of the backend half were vacuous and looked perfectly fine. A regex
  stripping Python comments (`#[^\n]*`) also eats `"#abcdef"` inside a string, which is
  the thing being searched for; and skipping docstrings by "the previous token ended a
  statement" also skips the second line of an implicitly-concatenated string. It is
  `ast`-based now, and each language's guard has been checked to fail on an injected
  colour — including inside an f-string and across a concatenation — and to stay quiet
  for comments and docstrings.

### Order of work

1. ~~**Delete** the dead `_HTML` block in `calibrate.py`.~~ **Done** — 166 → 34 lines.
2. ~~**Delete** `SETUP_HTML` in `server.py`~~ **Done** — 152 lines, the last apparent
   competing palette. The unused `send_html_file_with_nav` on the request handler is
   the remaining leftover from the same era.
3. ~~**Introduce** `design/tokens.json` + generator, emitting exactly today's values.~~
   **Done** — and it went further than planned: the element base is shared too, so the
   only hand-duplicated CSS left between the paths is the `@font-face` pair that must
   differ. Verified: both paths' rendered CSS is rule-for-rule unchanged.
4. ~~**Lift** the page-local `:root` additions and the CSS/TS hex literals into the
   JSON~~ **Done for the frontend.** 81 tokens in the generated set; **zero** colour
   literals left in `frontend/src/styles/*.css` or in `.ts`/`.tsx` outside the
   generated modules. The two name collisions were resolved by naming rather than by
   picking a winner: the wizard's palette became `--cat-*` (a separate
   work-category set, consumed by name from `SemanticWizard.tsx`) and the changelog's
   second `--app` became `--chg-design`. Verified by the pixel gate — 48 routes, 0
   pixels changed.
4b. ~~The 68 backend literals~~ **Done.** `backend/tokens.py` now also exports `VALUES`
   and eight palettes, and `render.py` / `store.py` / `collect.py` /
   `semantic_metrics.py` read from them. Also removed on the way: 54 lines of dead
   page rendering in `views_catalog.py` (a fifth copy of the `/views` CSS) and 14
   stale `var(--token,#hex)` fallbacks in `shell.SHELL_CSS`.
5. **Then** the visual decisions become one-file edits: fix `--mut` and the status
   colours to pass AA; collapse the radius set; add the dark palette.

Steps 1–4 carry no design decision and no visual change. They are worth doing
whether or not the Fabric kit is adopted.

### How step 4 was verified

`frontend/visual/` is a Playwright + pixelmatch harness over 48 route/state
screenshots at a 0.1% threshold. The sequence was: capture a baseline before touching
anything, then re-capture and diff after each batch. It earned its keep immediately —
the first attempt at stripping `var(--token, #fallback)` fallbacks assumed every
token was global, and `/views` came back **8.87% changed**, because `--code-bg`,
`--code-fg`, `--c-bug` and `--acc-soft` were defined in *other* pages' stylesheets. On
`/views` the fallback was the live value, and removing it turned the dark code block
transparent. 24 fallbacks were genuinely dead and went; 15 of those had a value that
already disagreed with the real token (`--panel2`'s fallback said `#eaeef2`, the token
says `#eef1f5`), which is what stale copies look like.

Re-run it with a server on demo data:

```bash
python3 backend/reportctl.py demo-seed && python3 backend/reportctl.py serve --port 8099
cd frontend && node visual/capture.mjs --base http://127.0.0.1:8099 --out visual/candidate && node visual/diff.mjs
```

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
