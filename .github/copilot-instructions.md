# Copilot instructions — Fabric CU Calculator

Single-page **static web app** that estimates Microsoft Fabric Capacity Unit (CU) consumption,
CU-hours, cost, and throttling exposure. Entire app lives in one self-contained `index.html`
(HTML + CSS + JS, no dependencies). Hosted on GitHub Pages.

## Build / run / verify

- **No build step, no bundler, no package.json.** Open `index.html` directly in a browser, or serve
  the folder statically. Keep it this way — GitHub Pages serves the root `index.html` as-is.
- **No committed test/lint tooling.** When changing the calculation logic, verify the engine the way
  it was validated originally:
  - Math check: replicate the formula in Node and compare against the worked examples in
    `Fabric-CU-Consumption-Reference.md` (e.g. F64 background 282,240 CU(s) → 78.4 CU-h, $14.11, 5.1% load).
  - Runtime/DOM check (optional): load `index.html` with `jsdom` (`runScripts: "dangerously"`, stub
    `window.matchMedia`), trigger input/change events, and assert the `#r_*` result fields update with
    no thrown errors.
- **Deploy:** commit and `git push` to `main`; GitHub Pages auto-redeploys in ~1 min. Do not delete
  `.nojekyll`.

## Architecture (the big picture)

- **`Fabric-CU-Consumption-Reference.md` is the source of truth** for every formula, rate, and SKU
  number. The JS in `index.html` must stay numerically consistent with it. If you change one, change
  both; cite the doc when adjusting constants or rates.
- **Calculation pipeline** in `index.html`:
  1. `getTotalCUSeconds()` — reads the active estimator's inputs and returns `{ cus, note }`
     (Total CU-seconds + a human-readable formula trace).
  2. `recompute()` — the single render function. Derives effective CU-hours, cost, smoothing
     per-timepoint load, throttling status, and headroom, then writes every `#r_*` output element and
     the formula trace. It is re-run on every relevant input event (no framework, no virtual DOM).
- **`SKUS` array drives both** the SKU `<select>` and the reference table — add a SKU in one place.
- **`REGIONS` object** (groups → `[name, pricePerCUHour]`) drives the Region `<select>` and is the
  per-region PAYG price source. Selecting a region auto-fills `#price`; a manual `#price` edit flips the
  region to **"Custom rate"**. Keep `REGIONS` numerically in sync with **Appendix D** of the reference
  doc (both come from the Azure Retail Prices API, `serviceName eq 'Microsoft Fabric'`).
- **`GLOSSARY` object** (`key → {term, def, url}`, ordered by `GLOSSARY_ORDER`) is the single source for
  both the inline term tooltips and the collapsible **Glossary card** (`#glossaryList`). The popover keys off
  any element with a `data-term` attribute (delegation uses `[data-term]`): plain inline markers are
  `<span class="term" data-term="KEY" tabindex="0">…</span>`, and operable controls (e.g. the operation-type
  `<button data-term="KEY">`) are term hosts too — those show on hover/focus but **do not pin** on click
  (their own click action runs and the tooltip is dismissed). The shared `#termPop` popover is built by the
  `glossary()` IIFE (hover/focus/tap, pin-on-click for non-button terms, Esc/outside-click/resize to close).
  To add a term: add a `GLOSSARY` entry (+ `GLOSSARY_ORDER`) and, if inline, wrap the word in a `.term` span
  (or add `data-term` to a button). URLs point at `learn.microsoft.com/fabric/enterprise/*`; verify each
  returns HTTP 200. Popover/card text is set via `textContent` (XSS-safe) — keep `<` out of definitions.
- **Spell out abbreviations:** write **operation(s)**, never "op/ops"; use **CU(s)** for CU-seconds.

## Key conventions

- **The deep-dark theme (`web-artifacts-builder` skill) is mandatory and must not be weakened:**
  - **Dark-only.** There is no light variant, no theme-detect script, no `data-theme` attribute and
    no theme toggle. Keep the `:root` palette block byte-exact.
  - **All colors use `var(--*)`** — `--bg --panel --panel2 --line --code-bg --ink --muted --dim
    --accent --green --red --amber --orange --code-ink --code-block` — including colors set from JS
    (the load bar uses `"var(--red)" / "var(--amber)" / "var(--green)"`). The only permitted literals
    are the one hero wash `linear-gradient(160deg,#131c28 0%,#0d1117 70%)` and the derived
    `--wash-* / --row-hover` tints, which are themselves copied from the skill.
  - Fonts: body `16px/1.6 "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif`;
    mono `"Cascadia Mono", Consolas, monospace` at 13–13.5px. Never Inter, Geist or `system-ui`.
  - Cards `border-radius: 14px; padding: 26px 28px`; code blocks 10px; inline code 5px; pills 999px.
    Borders are always `1px solid var(--line)`. **No drop shadows, glassmorphism or gradients**
    beyond the single hero wash.
  - The skill is versioned and has changed before (it replaced an earlier light/dark "Clawpilot"
    rose theme). **Re-read `SKILL.md` from disk before any theming work** rather than trusting cached
    context.
- **Adding/changing an estimator requires three synchronized edits**, or it silently breaks:
  1. an `<option value="X">` inside `#mode`,
  2. a `<div class="estimator-fields" data-est="X">` input block,
  3. a `case "X":` in `getTotalCUSeconds()`.
  Background-only estimators (Copilot, Cosmos, Spark, ADF) auto-force the Background class via the
  `backgroundModes` list in the `#mode` change handler.
- **Smoothing constants encode Fabric's model — don't change without doc backing:**
  `TP_SECONDS = 30`, `TP_PER_DAY = 2880` (24 h), interactive smoothing `10–128` timepoints (5–64 min),
  `SEC_PER_HOUR = 3600`. Core invariants:
  `effective_CU_hours = Total_CU(s) / 3600`; interactive per-timepoint `= Total / N`, background
  `= Total / 2880`; `load% = per_timepoint_CU(s) / (base_CU × 30)`.
- **Pricing is region-driven, not authoritative.** The Region dropdown sets `#price` from `REGIONS`
  (USD/CU-hour, East US = $0.18 default). Rates can change — keep the link to the Fabric pricing page and
  the "edit to override" hint; when refreshing rates, update both `REGIONS` and Appendix D together.
