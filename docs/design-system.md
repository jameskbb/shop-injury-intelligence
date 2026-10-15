# Design System: Shop Drawing

The design contract for the visual rebuild, written and frozen before implementation so every
page is built against one system instead of several.

**Token source of truth is `src/wii/app/theme.py`.** This document explains *why*; the module is
*what*. No colour literal may live anywhere else.

---

## The brief

- **Subject.** Twenty years of U.S. emergency-department surveillance data (NEISS), read as:
  which workshop tools show up in ER-treated injuries, where on the body, how, how badly.
- **Audience.** Woodworking hobbyists first. People who own a table saw and want to know what
  actually happens to people who own a table saw.
- **Primary job.** Make injury *burden* and injury *severity* legible and clearly distinct, and
  make the honest reading the easy one. NEISS has no exposure denominator, so nothing here may
  imply per-use risk.

## The direction: a measured shop drawing

The design lives in the vernacular of a **woodworking plan**: a drafted sheet with a title block,
a datum edge, dimension lines, measured callouts and a cut list. This is the audience's own visual
language (the thing they already have taped to the shop wall), and it is a language built for
exactly what this app does: state measured quantities precisely and show where on an object they
apply.

It is a *drawing*, not a photograph and not a UI kit. That single commitment resolves the app's
loudest complaint: the 3D figure stops trying to be a person (and failing, in the uncanny valley)
and becomes a drafted orthographic plate: matte, constructed, obviously a diagram.

---

## Tokens

### Colour: 6 named values

| Token | Hex | Role |
|---|---|---|
| `VELLUM` | `#E8EAE5` | The sheet. Cool grey-green drafting film. |
| `VELLUM_DEEP` | `#DADDD5` | Secondary surface: sidebar, table banding, expander bodies. |
| `GRAPHITE` | `#22272A` | Primary ink. All body text and linework. |
| `GRAPHITE_SOFT` | `#6B736F` | Secondary ink. Axis labels, captions, quiet data. |
| `BLUEPRINT` | `#1C5B7D` | The one data accent. Drafting blue pencil. Selection, focus, the sequential ramp's deep end. |
| `REDLINE` | `#A83E27` | Correction ink. Genuine cautions and watch-outs only, never magnitude, never a category. |

Derived, never new literals: `RULE` = graphite @ 22% (hairlines), `GRID` = graphite @ 10%
(graph paper), `CONSTRUCTION` = graphite @ 38% (dashed provisional geometry).

**Amber is retired from the product.** It previously meant "limited sample", was drawn at
0.55–0.6 alpha, and was therefore the loudest element on four of nine pages; the app's dominant
visual signal was "we don't trust this". See the confidence rule below for what replaces it.

### The two encodings that carry the whole product

This is the part that comes *from* the direction rather than being bolted onto it.

**1. Ink density = magnitude.** More ink, more injuries. The sequential ramp is one hue family
travelling from nearly-bare vellum to saturated blueprint:
`#DCE3E4 → #A8C2CE → #6D9BB4 → #3A7192 → #134A66`.

**2. Line treatment = confidence.** On a real drawing, solid lines are finished geometry and thin
dashed lines are provisional construction. A drafter reads that instantly, and it is exactly the
distinction this dataset needs:

| Stability | Treatment |
|---|---|
| Stable | Solid 1px graphite outline, filled at the value's ink density. |
| Limited sample | Same fill, lightened one step; **dashed** construction outline. |
| Suppressed | **No fill** (bare vellum); dashed outline only. Never a colour. |
| No cases | No fill, no outline; region label greys out. |

Confidence is never colour-coded. Magnitude is never line-coded. They are orthogonal channels, so
a cell can say "large but provisional" without ambiguity, which the old amber hatch could not.

**A stable region carries no contour where its fill can stand alone.** Outlining every region turns
a body figure into a paint-by-numbers grid and re-creates the segmented-mannequin look this design
exists to remove. The governing rule is narrower than "no contour on stable": **a region must be
distinguishable from bare sheet.**

- The **3D figure** is lit and shaded, so fill alone clears that bar. Stable regions get no contour.
- The **flat SVG** has no lighting, and the bottom half of the ramp measures 1.07:1 to 2.48:1
  against bare vellum, all under 3:1. A small stable value drawn with no contour would be
  indistinguishable from a suppressed one, so the figure would state the opposite of the truth.
  There, a solid graphite hairline supplies what shading otherwise would.

Same principle, two implementations, because the media differ.

**Dashes need room per region, so only the full-size body map draws them.** Measured on three
figures: the Comparison signature (17 dashed regions against 2 carrying ink), My Shop's "Protect
this" and Tool Profiles' anatomy snapshot (both ~20 against 2). The dashes outnumber the data by
about 10:1 and stop being signal. They become texture, and the figure reads as a ghostly skeleton:
the same defect as the original amber hatch, redrawn in a new medium.

The deciding factor is the ratio, not the pixel width, but the reliable encoding of it is simple:
**the full-size Body Map figure opts in; every other body figure in the app opts out.** An opted-out
figure falls back to one quiet hairline per region, fill becomes the only variable, and the floor
count is carried in the caption. Nothing is hidden: every region still names its own stability on
hover, on every figure.

`render_body_map` takes `confidence_outlines`, `callouts` and `floor_note`, set explicitly at each
call site rather than by a threshold inside the component, so the decision is visible where it is
made instead of being a number someone has to rediscover.

Diverging (ratio vs. a baseline) runs blueprint → neutral → redline:
`#1C5B7D → #7FA3B5 → #CFD3CC → #C98A72 → #A83E27`. Both poles are non-amber, fixing the review's
finding that the warm pole and the caution marker shared a hue.

### Category palettes: three non-overlapping ink families

The review found `laceration`, `direct_cutting_contact` and the `saws` family all resolved to
`#E8735C`, and that they co-occur on one screen. Fixed by giving each dimension its own family:

- **Diagnosis** (what the injury is): warm inks: sienna, maroon, ochre, violet, viridian, slate,
  mulberry, payne's grey.
- **Mechanism** (how it happened): a cool teal→indigo family, entirely disjoint from diagnosis.
- **Tool family**: neutral earth/graphite, so it never competes with either.

No hex appears in two palettes. No palette member equals `BLUEPRINT` or `REDLINE`.

### Type

**Archivo** throughout, with **Archivo Narrow** reserved for measured numerics, axis labels and
title-block metadata. One superfamily, two widths. Narrow gothic is what drafters actually
lettered dimensions in, and it lets the app carry dense tabular figures without reaching for a
typewriter face.

Scale (major third, 1.25, base 16): micro 12 · label 13 · body 16 · subhead 19 · heading 24 ·
display 36 · measured value 34. Tabular lining numerals everywhere a figure appears.

### Layout

- **Datum edge.** Everything left-aligns to one rule. Drawings align to a datum; nothing is
  centred except inside the 3D stage.
- **Title block.** Each page opens with a thin strip stating what is drawn, the basis, the filters
  in force and the data vintage. This replaces the scattered filter captions and is *information*,
  not chrome.
- **Dimension lines do real work.** A KPI is not a number in a box. It is a measured value with a
  rule beneath it spanning what it covers: `|← 2006–2025 →|` under a period total makes the
  period explicit at the point of reading, which is exactly the honesty the brief demands.
- **No boxes.** Hairline rules and whitespace separate content. Radius 0. **No shadows anywhere**;
  a drawing does not cast one.
- Faint graph-paper grid behind drawing areas only, never the whole page.

### Motion

Motion answers user action and nothing else. The one orchestrated moment is the body-map camera
flight when a tool is selected; it teaches where that tool's damage concentrates, so it is doing
analytical work. No entrance animations, no per-card hover transitions, `prefers-reduced-motion`
respected throughout.

### Where the boldness is spent

**The 3D figure.** It is the one memorable element; everything around it stays quiet and
disciplined. Chanel's rule applies to every other surface.

---

## Self-critique against the generic-AI defaults

The skill names five clusters that AI design falls into. Checked honestly:

1. **Warm cream + high-contrast serif + terracotta (~`#D97757`).** The nearest miss, because
   "paper" invites it. Avoided deliberately: the ground is *cool* `#E8EAE5` drafting film, not warm
   cream `#F4F1EA`; there is no display serif; the accent is blueprint blue, not terracotta. The
   one red is an oxide red-pencil `#A83E27`, kept well away from `#D97757` and used only as
   annotation, never as a decorative accent.
2. **Near-black + one acid accent.** Not applicable: this move goes light. *(This is the single
   consequential swing in the rebuild: the app is dark today. It is one token block and one
   config file, so it is cleanly reversible to a blueprint-dark variant of the same system.)*
3. **Broadsheet: hairline rules, zero radius, dense columns.** Genuine partial collision: this
   design does use hairlines and radius 0. Kept, because they are load-bearing here rather than
   stylistic: a drawing is made of hairlines, and a drawing sheet has square corners. Steered away
   from the tell by having no masthead, no justified multi-column prose, and by using a datum edge
   and dimension lines: drafting devices, not newspaper ones.
4. **The SaaS-card kit.** Explicitly banned: no uniform rounded cards, no single radius on
   everything, no `rgba(0,0,0,.1)` shadow, no gradient washes. The existing KPI/insight/watch-out/
   case cards all lose their box and become rule-separated blocks. This is the largest single
   change to the existing CSS.
5. **Template chrome.** Banned: no tracked-out ALL-CAPS eyebrows, no `A · B · C` middle dots, no
   `WORD, fragment` spaced-dash constructions (no em dashes anywhere in the product), no `→`
   appended to links. Tinted near-black is avoided;
   `#22272A` is used as *ink on paper*, never as a background. **And the monospace-for-small-data-
   labels tell is removed outright:** the existing `FONT_MONO` on every KPI value and table cell
   is replaced by Archivo Narrow, which is both less generic and more authentic to the subject.

**What I changed after this critique:** dropped the planned IBM Plex Mono for measured values
(tell 5) in favour of Archivo Narrow; moved the ground off warm cream to cool vellum and re-picked
the accent away from terracotta (tell 1); and removed every card background, border-radius and
shadow from the component CSS rather than merely restyling them (tell 4).

**Accepted risk:** two surfaces use capitals, and only two. The skill warns against all-caps labels
and against the tracked-out caps eyebrow above a heading; both warnings are respected everywhere
else: sentence case throughout, enforced by a test that scans every heading on all nine pages.

1. **The title block keys.** It quotes a real artifact, appears exactly once per page, and is the
   device the whole layout is named for.
2. **Table column headers** (`.wii-comparison-strip th`). Small caps on a column header is a
   conventional table device, not an eyebrow, and it is the right register for a cut-list.

Everything that failed this test has been fixed at the source rather than with CSS, so the real
text is what screen readers and the guardrail scanner see: My Shop's four section headings, and the
Signals tier names, which turned out to be capitalised in `insights.yaml`, meaning the shouting had
reached the data itself. The badge also lost its `letter-spacing: 0.08em`; with the caps gone, the
tracking was the other half of the tell.

---

## Quality floor

Responsive to 390px with no horizontal scroll · visible keyboard focus (blueprint, 2px) · WCAG AA
contrast for all text and for every data mark against vellum · `prefers-reduced-motion` honoured ·
every estimate still labelled "Estimated U.S. ER-treated injuries" with period vs. annual-average
explicit · suppression still unmistakable to a careful reader. Quiet is not hidden.

---

## Upstream reference: the originating build spec

`productivity-prompts/system-prompts/master-dashboard-builder.md` is the generalized prompt this
project was generated from; the repo's `src/` layout, the Signals page, the Methodology &
data-quality page and the deterministic narrative engine all trace to it. It is a `{{PLACEHOLDER}}`
template, so it constrains rather than dictates, and where it meets this document, **this document
governs the visual layer and the build spec governs the product requirements.**

What it adds that the adversarial review did not cover:

- **Every KPI carries comparison context**: selected period, previous equivalent period, delta,
  and prior-year where the data supports it. Rates compare in **percentage points**, never as a
  percent change of a percent.
- **Metric direction is explicit in the semantic layer.** In this dataset *lower is better* for
  essentially every metric (a falling injury estimate is good news), and direction must never be
  carried by colour alone.
- **Source and freshness are visible and never hard-coded**, computed from the actual maximum
  period in the processed data. This lives in the title block, which is what a title block is for.
- **Empty states and metric tooltips** are first-class reusable components, alongside KPI cards,
  comparison indicators, section headers, filter context and signal cards.
- **Consistent number formatting** from one shared layer: `2,418,391` · `78.4%` · `+3.2 pts` ·
  `14.2M`.
- **Chart contract:** every chart needs a title, a useful tooltip, units, sorting, sane axis
  formatting and context where required. Preferred forms are line, ranked horizontal bar, heatmap
  and scatter/bubble. Avoid pie charts, gauges, rainbow palettes and giant legends.
- **Language guardrails:** "associated with" rather than causal phrasing; "reported *category*";
  "within the NEISS reporting population"; "selected period".

### Two tensions, resolved

**1. Showcase quality vs. the pre-public sweep.** The build spec asks for showcase quality and
a "Why this exists" README section; the review says strip reviewer-facing framing before going
public. These separate cleanly: **keep the quality bar and a short, honest "Why this exists"; cut
the copy that addresses a reviewer of the author.** The boast goes, the "five strongest features"
list goes, the short personal paragraph stays. De-brag it, don't de-personalise it. The spec
itself asks for that section to be "short and professional" and to not read as generated.

**2. "Compact KPI cards, subtle borders" vs. no boxes.** The build spec suggests cards; the
frontend-design skill names the uniform-rounded-card kit as a generic-AI tell, and this design
replaces boxes with hairline rules. The spec's *load-bearing* requirement is not the box; it is
**density**: "dense enough to be useful", "no giant hero banner wasting screen space". That
requirement is binding. Removing card chrome must yield **more** information per screen, not less.
If a restyled page fits less above the fold than the version it replaced, the restyle is wrong.

The spec's "3D charts" prohibition means extruded bars and pies, not the anatomical model; the
model is the designated signature visual. Its "unnecessary animation" prohibition stands: the
camera choreography survives only because it is user-triggered and analytical. No idle drift, no
ambient rotation, no decorative easing.

### Definition of done (the release gate)

From the build spec, checked before landing: app runs clean · filters work · KPIs reconcile ·
charts interactive · signals credible · tests pass · lint passes · freshness shown · methodology
documented · source credited · no secrets · README ready for a stranger · UI cohesive · no placeholder
sections · no fake metrics presented as real · **the result looks substantially better than a
default Streamlit dashboard.**
