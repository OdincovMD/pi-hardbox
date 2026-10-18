---
version: alpha
name: "PI Hardbox Telemetry"
description: "A compact public instrument panel for a Raspberry Pi home server."
colors:
  ink: "#16211d"
  ink-soft: "#32423a"
  paper: "#e7ece5"
  paper-bright: "#f4f6f1"
  rule: "#a9b6ab"
  raspberry: "#b62d4d"
  leaf: "#4d7665"
  amber: "#a96f24"
  focus: "#176c94"
typography:
  display:
    fontFamily: "Georgia, 'DejaVu Serif', serif"
    lineHeight: "0.84"
  data:
    fontFamily: "'DejaVu Sans Mono', 'Liberation Mono', monospace"
  body:
    fontFamily: "'Trebuchet MS', 'DejaVu Sans', sans-serif"
rounded:
  sm: "0.4rem"
  md: "0.8rem"
spacing:
  page-gutter: "1rem"
  section-gap: "0.875rem"
  page-max: "70rem"
components:
  telemetry-panel: {}
  metric-row: {}
  live-status: {}
  machine-passport: {}
---

# PI Hardbox Telemetry Design System

## Overview

The creative North Star is a well-kept field instrument: calm mineral surfaces, exact engraved-looking labels, and one raspberry-coloured signal line. The page serves a Russian-speaking server owner who needs an immediate public read of hardware health on desktop or phone. It is a product surface, not a marketing page.

The memorable signature is the two-minute CPU pulse trace paired with the oversized uptime statement. Restraint applies everywhere else: no glossy gradients, neon terminal clichés, glass cards, stock icons, or decorative controls. The runtime CSS variables embedded in `dashboard.py` are canonical; this document mirrors their accepted values.

## Colors

`ink` and `paper` establish the instrument casing. `raspberry` identifies live measurements and recalls the hardware without becoming a full red theme. `leaf` means normal operation, `amber` means pending or attention, and `focus` is reserved for visible keyboard focus. Text and status never rely on colour alone.

## Typography

Georgia is used only for the human-readable state headline. Its responsive size is owned by runtime CSS (`clamp(3.15rem, 8vw, 6.7rem)`); it is omitted from scalar frontmatter because the DESIGN.md validator does not accept CSS functions there. DejaVu Sans Mono carries measurements, timestamps, and labels with tabular stability. Trebuchet/DejaVu Sans carries short explanatory copy. Fonts are system-resident to avoid an external request and late font swap.

## Layout

The desktop composition is a wide instrument face: status statement and thermals first, three equally weighted metrics second, the trace third, compact facts, then a two-column machine passport. At 780px the passport intro stacks above its data; at 460px every passport cell becomes a single readable column. Async values reserve their line boxes, so refreshes do not move controls or sections.

## Elevation & Depth

Hierarchy comes from tonal blocks and rules. Only the primary dark instrument face has a restrained shadow. Metric and fact regions remain flat; nested card stacks and frosted-glass effects are forbidden.

## Shapes

Major housings use the `md` radius, while signals and progress tracks are circular. Internal sections use square rules instead of independently rounded cards.

## Components

The live status always combines signal colour with text. Native progress elements provide accessible values and stable geometry. The pulse trace is descriptive and has a changing accessible label. The machine passport uses one semantic description list and the existing rule/elevation grammar rather than a new card style. Initial loading uses em dashes in reserved slots; a failed refresh preserves the last measurements and clearly labels the connection as unavailable.

Per-core CPU measurements extend the same progress/SVG vocabulary in one flat panel with ruled subdivisions. Four equal columns become two below 900px; individual readings use a decimal percentage, core number and Linux CPU ID. Each miniature trace has the same fixed 0–100% and two-minute scales, with a current-point marker and an accessible text summary. Raspberry identifies measurements, and text explicitly labels high load. The graph history grows only while the page is open; missing measurements create gaps. Reserved grid geometry and tabular values keep updates stable. Runtime colors and type families remain unchanged.

Motion is limited to progress interpolation. Reduced-motion preference removes meaningful duration. The cooling section extends the instrument panel with native password, radio and range controls inside a disclosure. Buttons, focus and feedback use the existing ink, paper, raspberry and focus tokens; no new palette or elevation is introduced. Inline live feedback reserves its line box. Controls are disabled while applying, offline or without an owner code. The selected speed remains an editable draft until explicitly applied. Credentials stay only in page memory; public readings and protected control share the existing layout. There are no tables, overlays or additional navigation beyond the canonical home link.

The owner-requested VPN route adds two compact native navigation links under the masthead, using the existing raspberry underline and aria-current. It reuses the dark instrument face, flat facts, description lists and owner disclosure controls. Secret inputs share show/hide, busy and inline feedback patterns. Both routes retain natural document height; no palette or typography values change. This route navigation extends the earlier single-page home-link constraint; there are still no tables or overlays.

Domain routing extends the same flat description-list panel with aggregate
counters, source dates and explicit route labels. A single owner disclosure
contains the existing key form and two labeled native domain textareas, with
two columns becoming one on phones. Textareas inherit the existing paper, ink,
rule, radius and focus tokens; no new durable tokens or dependencies are added.
The canonical form owner remains dashboard.py and the behavior contract remains
UX-CONTRACT.md. Only protected owner reads populate editable domain lists.

## Do's and Don'ts

- **Do:** keep measurements legible, tabular, and paired with units or explanatory text.
- **Do:** preserve the single-file, dependency-free runtime and global token ownership.
- **Don't:** expose IP addresses, process names, secrets, or container names on the public endpoint.
- **Don't:** add generic admin navigation, ornamental cards, charts without accessible summaries, or third-party assets.

## Three-route management extension

Owner request 2026-10-17 establishes exactly three peer links: Дашборд (/),
VPN (/vpn), Управление (/manage). Fan controls move to management; telemetry
retains its existing layout. A shared compact sudo-login disclosure under the
navigation gives a single consistent auth/session owner across all routes.
Autostart reuses the ruled description-list vocabulary. Reboot is separated
from safe controls and confirmed through a styled native HTML dialog using
existing colors, radii, type and focus tokens. No new palette or typography.
The native top layer owns stacking and modal focus/inert behavior. This extends
the earlier no-dialog constraint specifically for the requested reboot action.

## Control alignment revision

Runtime CSS remains canonical. `--control-height: 44px` and
`--control-gap: 8px` map directly to the shared button/input recipe and action
groups in dashboard.py; colors, radii and type retain their existing owners.
Fields/reveal buttons share a grid row, with the login action on that row on
desktop and its own full-width row below 620px. Form content shares a 52rem
maximum; actions align to the same left edge. Routing utility actions use an
equal-column group with a reserved 64px height for long/dirty/loading labels.
Primary actions become full-width on phones. Dialog actions share the same
control recipe. Root disclosure panels have no redundant top rule/padding.
Domain inputs use native content sizing, bounded to 11–22rem with shared visible
scrollbars. The dashboard has no login panel; authentication is presented on
VPN and management only, as explicitly requested by the owner.

## Status lamps and project cards

One shared status-light DOM/CSS recipe owns service and project indicators.
Its small static lens uses existing leaf/raspberry/amber/muted tokens and a
restrained rim; text names the state so color is never the only cue. Service
readings distinguish current work from boot enablement. Unknown fetches are
gray, without blinking. Project cards reuse flat ruled sections and description
lists: name/overall state, running containers, HTTP and autostart, then an exact
healthcheck summary. The two existing routes share one renderer and monitor.

The existing PI monogram is adapted in code for SVG/ICO/touch icons. The generated
link cover uses the same mineral/ink/raspberry palette and a Raspberry Pi server
illustration; its prompt and saved asset paths are in assets/README.md. These
assets identify the browser tab/bookmarks and shared link previews; they add no
new navigation, login surface or settings action.


img2txt extends the existing project renderer on both dashboard and management.
Its additional labeled lights distinguish actual VPN reachability from primary
key synchronization. They reuse the shared status-light and ruled readings;
there is no new login, palette, control, or public application API link.

The img2txt LLM API light names missing/invalid provider credentials separately
from VPN state. Overall attention remains visible while generation is unavailable,
even when containers, HTTP, boot enablement and VPN are all green.


Subscription monitoring extends the VPN description list and shared static lamp.
The existing owner disclosure adds one native form using the same action grid,
masked secret/reveal row, checkbox and labeled number input. Its 44px control
height, 64px utility rows, typography, colors and focus come from the canonical
runtime recipes. Narrow screens stack the same actions without new tokens.
