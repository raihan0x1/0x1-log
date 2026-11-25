---
name: 0x1LOG
description: Embedded observability dashboard drawn as an instrument console, where amber marks what the operator did, cool blue marks what the app measured, and red marks what broke.
colors:
  # graphite (dark). Light mode is the paper colourway below.
  paper: "oklch(17% 0.008 250)"
  paper-2: "oklch(21% 0.009 250)"
  paper-3: "oklch(13.5% 0.007 250)"
  ink: "oklch(95% 0.005 250)"
  ink-2: "oklch(83% 0.008 250)"
  muted: "oklch(71% 0.01 250)"
  rule: "oklch(31% 0.01 250)"
  rule-2: "oklch(42% 0.012 250)"
  accent: "oklch(82% 0.15 80)"
  accent-ink: "oklch(18% 0.04 75)"
  accent-soft: "oklch(29% 0.05 80)"
  focus: "oklch(82% 0.15 80)"
  cool: "oklch(77% 0.12 245)"
  cool-ink: "oklch(84% 0.09 245)"
  cool-soft: "oklch(29% 0.05 245)"
  ok: "oklch(76% 0.15 150)"
  ok-ink: "oklch(84% 0.12 150)"
  ok-soft: "oklch(24.5% 0.03 150)"
  bad: "oklch(70% 0.17 25)"
  bad-ink: "oklch(82% 0.11 25)"
  bad-soft: "oklch(24.5% 0.035 25)"
  brand-1: "#78a4ff"
  brand-2: "#b896ff"
  paper-light: "oklch(98% 0.006 85)"
  paper-2-light: "oklch(99.6% 0.003 85)"
  paper-3-light: "oklch(95.5% 0.008 85)"
  ink-light: "oklch(19% 0.01 60)"
  ink-2-light: "oklch(32% 0.01 60)"
  muted-light: "oklch(46% 0.012 60)"
  rule-light: "oklch(89% 0.008 85)"
  rule-2-light: "oklch(76% 0.01 85)"
  accent-light: "oklch(54% 0.14 70)"
  accent-ink-light: "oklch(99% 0.004 85)"
  accent-soft-light: "oklch(93% 0.05 80)"
  cool-light: "oklch(52% 0.14 250)"
  cool-ink-light: "oklch(44% 0.14 250)"
  cool-soft-light: "oklch(94% 0.03 250)"
  ok-ink-light: "oklch(40% 0.11 150)"
  ok-soft-light: "oklch(94.5% 0.04 150)"
  bad-light: "oklch(54% 0.19 25)"
  bad-ink-light: "oklch(45% 0.17 25)"
  bad-soft-light: "oklch(95.5% 0.03 25)"
typography:
  body: { family: "Host Grotesk", size: 14px, weight: 400, line-height: 1.5 }
  label: { family: "Host Grotesk", size: 12px, weight: 500 }
  title: { family: "Host Grotesk", size: 16px, weight: 500 }
  verdict: { family: "Host Grotesk", size: 2.074rem, weight: 500, tracking: -0.03em }
  wordmark: { family: "Host Grotesk", size: 16px, weight: 600, features: '"calt" 0' }
  measured: { family: "Atkinson Hyperlegible Mono", size: 12-14px, weight: 400, numerals: "tabular slashed-zero" }
  reading: { family: "Atkinson Hyperlegible Mono", size: 1.728-2.074rem, weight: 400, tracking: -0.03em }
rounded:
  card: 10px
  control: 6px
  chip: 4px
spacing: [2px, 4px, 8px, 12px, 16px, 24px, 40px, 64px]
components:
  bar: "52px top bar on paper-3, a 1px rule beneath; tabs underline in amber"
  query: "search field 40px on paper-3 with the Search button beside it; filters 36px on the row beneath"
  ledger: "one paper-2 surface cut by 1px rules: count, volume strip, table, scroll row"
  table-row: "36px; time and trace id in cool mono, kind as a 3px keyed bar and its label, level as a chip"
  chip: "label face or mono inside a 1px rule at 4px; tint carries state only"
  inspector: "non-modal side panel on paper-2, a rule-2 edge, code and headers on paper-3"
  panel-group: "stats and charts share one surface divided by 1px seams"
---

# Design System: 0x1LOG

## Overview

**Creative North Star: "The Instrument Console"**

The dashboard is an instrument kept by careful people. A graphite ground is stepped in three lightnesses, panels are cut from one surface by hairlines, and nothing glows or floats. Two meanings sit on the neutral ground at equal weight: **amber is what the operator did** (the primary action, the focus ring, the selected row, live tail when it runs, an applied filter) and **cool blue is what the app measured** (timestamps, trace ids, durations on charts, a POST). Red is the only state colour that rises: a WARNING is a red outline, an ERROR a red tint, a CRITICAL a red block. Green appears for success codes and good service only.

Host Grotesk carries everything a person reads. Atkinson Hyperlegible Mono carries everything the app wrote: times, ids, paths, durations, counts and every figure, always tabular, with its slashed zero. Mono is never a label, a heading or a button.

Density is the design: 36px rows, 36px controls (40px for the search), a 14px body. The light theme is the same system on paper: a warm near-white ground, ink in place of pale ink, amber and cool darkened to hold 5:1.

This replaced the transit-map system (navy signage band, Overpass, ring stations) in November 2026. The logo mark is kept as the brand.

**Key characteristics**
- One surface per idea: the ledger (count, volume, table, scroll row), the stat strip, the chart pair, the trace facts and waterfall.
- Amber for the operator, cool for the instrument, red rising in three steps, green for success only.
- Mono for what was measured, the grotesk for everything a person reads.
- Flat: no shadow, glow or gradient on any surface.
- One authored motion: a trace draws itself once when it opens.

## Colors

Tokens live in `ox1log/dashboard/static/tokens.css` (graphite, on `:root`) and `styles.css` (the app's own roles, and the paper colourway under `[data-theme="light"]`). The inline script in `index.html` sets `data-theme` before first paint.

- **Ground:** `paper` for the page, `paper-2` for panels one step up, `paper-3` for the deepest step: the top bar, inputs, code, headers, the waterfall header.
- **Ink:** `ink` for content, `ink-2` for secondary content, `muted` for labels and meta. Every label and control measures 5:1 or more in both themes.
- **Rules:** `rule` divides a surface; `rule-2` outlines controls and chips and marks the inspector's edge.
- **Amber (`accent`):** the primary button, focus, selection, live tail on, applied filter chips, the new-events pill, the active tab's underline.
- **Cool:** times, trace ids, POST, `3xx`, requests on charts, request spans.
- **Bad:** WARNING (outline), ERROR and `5xx` (tint), CRITICAL and error rows (fill or row tint), errors on charts and the volume strip.
- **Ok:** `2xx` and good service.
- **Event kinds** (`--l-*`): a categorical set at one lightness for spans and the waterfall (llm, agent, tool, db, cache, http, auth, embedding, custom, job); `app.js` reads them by name.

**Named rules**
- Never swap the two meanings: an amber reading or a cool deviation is the system lying.
- No third signal colour; warnings rise within red.
- No colour literal outside the token blocks.

## Typography

- **Host Grotesk** (bundled, `host-grotesk.woff2`, variable 300 to 800): body 14px at 1.5, labels 12px 500, titles 16px 500, the Metrics verdict at 2.074rem 500 with -0.03em tracking. The wordmark sets `font-feature-settings: "calt" 0`: the face's contextual alternates otherwise draw "0x1" as "0×1".
- **Atkinson Hyperlegible Mono** (bundled, `atkinson-hyperlegible-mono.woff2`): 12px for times and durations in rows, 13px for paths, 14px for counts, display size for readings (error total, stat values).
- Fonts are bundled, never loaded from a CDN: the dashboard runs inside the user's app, offline and behind their CSP.

## Layout

- The container caps at 1440px and centres; gutters 24px (16px under 48rem).
- **Logs:** the query (search and Search on one row; level, type, range and rows filters beneath), then the ledger. Wider than 48rem and at least 36rem tall, the ledger fills the window and the table scrolls in its own box; phones and short windows keep the page scroll. Older batches of 50 rows load from a cursor as the row beneath the table comes within 600px, up to the rows filter (200, 1,000 by default, or all); at the cap the scroll row offers Show all rows. A batch more than 3,000px from view is parked as one spacer row of the same height and refilled from memory, so all rows cost what a few hundred do.
- **Live tail** refreshes the newest batch only while the reader is at the top and nothing older is loaded. Otherwise it counts new events into a pill under the sticky table head, and the reader's rows never move.
- **Inspector:** a 440 to 600px side panel; at 75rem and wider the main column gives way instead of being covered. Under 48rem it covers the screen.
- **Errors:** the total leads as a reading, then signatures as a ledger (count, signature in mono, last seen, message).
- **Metrics:** the verdict leads ("Severe delays"), then five readings on one surface, then the two charts on one surface.
- **Trace:** facts and waterfall on one surface.

## Elevation & Depth

None by shadow. Depth is the three paper steps and 1px rules. The status popover and chart tooltip sit on `paper-2` or `paper-3` with a `rule-2` edge.

## Shapes

Cards and panels 10px, controls 6px, chips and keycaps 4px, the new-events pill fully round. Bars in charts and the waterfall square off at 2px.

## Components

- **Top bar:** brand mark and wordmark, four tabs (active: ink with a 2px amber underline), then status, Lock and theme. Status is a chip whose tint is its state (good: ok tint; minor: red outline; severe and down: red tint).
- **Buttons:** primary amber fill with `accent-ink`; default `paper-2` with a `rule-2` outline; quiet transparent. Disabled drops to `paper` with a `rule` outline, muted ink and a not-allowed cursor; a primary in flight keeps its place on `accent-soft` with a progress cursor.
- **Fields:** `paper-3` inside a `rule-2` outline at 6px; focus turns the outline amber. Selects carry a muted chevron; with a fine pointer their picker opens as a `paper-2` panel with an amber check, and touch keeps the native picker.
- **Table rows:** hover `color-mix(ink 5%)`, selected `accent` 14% into `paper-2`, CRITICAL and 5xx rows on `bad-soft`, Repeat members indented on `paper`.
- **Repeats:** consecutive events with the same kind, name, level and status fold into one row with a count; opening one reloads only its batch.
- **Inspector:** kind, method and status chips; Copy JSON, Copy as cURL and View trace; facts as a key and value list; Request and Response each headed with header count and body size (Response also with its status chip), headers then body on `paper-3`. Redacted values arrive as `[REDACTED]` from the middleware.
- **Empty states:** what belongs here and what fills it, no illustration.

## Motion

`--dur-micro` (120ms) and `--dur-short` (220ms) on `--ease-out` for state changes; the inspector slides in over `--dur-short`. The trace waterfall draws once (`--dur-long`). The scroll spinner turns while a batch loads. Reduced motion stops all of it.

## Do's and Don'ts

**Do**
- Put every number next to its unit or its time.
- Keep a value in mono when the app wrote it, in the grotesk when a person reads it.
- Cut related panels from one surface with 1px seams.

**Don't**
- Float a card on a shadow, or put a gradient on a surface.
- Use mono for a label, a heading or a button.
- Add a third signal colour, or use amber for anything the app measured.
- Load a font or script from a CDN.
