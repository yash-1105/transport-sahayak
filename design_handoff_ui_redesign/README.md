# Handoff: Transport Sahayak — UI Redesign

## Overview
A full UI redesign of **Transport Sahayak** (https://transport-sahayak.vercel.app/) — a road-accident first-response app for the Guwahati NH-27 / AT Road corridor, Assam. The redesign fixes: (1) map clutter — all markers/popups rendered at once and blocked the map, (2) a flat, dated visual style, (3) a cluttered post-report results screen. **This is a reskin + layout restructure only. Do NOT change any application functionality, data flows, APIs, or business logic.** Also: enable the Network tab in the Vercel deployment (it exists in the codebase but is hidden there).

## About the Design Files
`Transport Sahayak Redesign.dc.html` (open it in a browser with `support.js` alongside) is a **design reference created in HTML** — an interactive prototype showing intended look and behavior. It is NOT production code to copy. Recreate this design inside the existing Transport Sahayak codebase (React + Google Maps JS) using its established patterns, components, and libraries.

The prototype is interactive: click the header tabs, layer switches, filter pills, "Report Incident", mode tabs, the location field, and "SEND SOS" (shows the post-report view).

## Fidelity
**High-fidelity.** Colors, typography, spacing, radii, and shadows are final — recreate pixel-perfectly. All values appear as inline styles in the HTML file and are listed below.

## Design Tokens

Fonts: `'IBM Plex Sans','Noto Sans Devanagari',sans-serif` (Google Fonts, weights 400–700). Devanagari font is required — every primary label shows English + Hindi.

Colors:
- Navy: `#0E1A2F` (900), `#14243E` (800, header gradient `180deg #14243E → #0E1A2F`), `#173B77` (700)
- Blue `#2456A6` — links, primary info, hospital markers, switches ON, focus
- Red `#C6362C`; CTA gradient `135deg #D14036 → #B92C22`; red-soft bg `#FBEAE8`, border `#EFC7C3`, text `#A32B22`
- Saffron `#DD8A20`; saffron-soft bg `#FBF1E4`, border `#EED9B8`, text `#8A5A17` (warnings, MEDIUM severity, brand gradient `135deg #E8862B → #C6362C`)
- Green `#1E7F4F`; green-soft bg `#EAF5EE`, border `#C4E2CF`, text `#175C3B` (success, ETA)
- Surfaces: page `#F3F1EC`, card `#FFF`, inset `#FAF9F5`, border `#E4E1D8`, hairline `#EFECE4`
- Text: ink `#1C2434`, body `#3B4658`, secondary `#5B6572`, muted `#8A8578`, faint `#B4AFA2`

Radii: cards 14px, sheet top 18px, inputs/rows 9–10px, chips/pills 99px. Shadows: side panel `0 8px 28px rgba(14,26,47,.12)`, bottom sheet `0 -12px 48px rgba(14,26,47,.3)`, FAB `0 8px 24px rgba(198,54,44,.4)`, map controls `0 2px 8px rgba(28,36,52,.1)`.

Type scale: page title 19/700, card titles 13.5–15/600–700, body 13/400, secondary 12–12.5, caps section labels 10.5–11/700 `letter-spacing:.08–.09em uppercase #8A8578`, stat numbers 24/700, ETA countdown 26/700 tabular-nums.

Bilingual rule: Hindi appears as a lighter/smaller suffix (`· हिंदी`) or sub-line under the English label, color `#8A8578` (or `#7D8DA9` on navy). Keep the EN/हिं header toggle.

## Screens / Views

### 1. Global shell
- 60px navy header (gradient above, inner top highlight `0 1px 0 rgba(255,255,255,.06) inset`): brand block, centered segmented nav, EN/हिं toggle right.
- Brand: 34px rounded-9px tile, saffron→red gradient, containing a white **shield + cross** line icon (stroke 2, 19px); title 15/600 white "Transport Sahayak" + subtitle 11px `#7D8DA9` "Guwahati, Assam (NH-27) — Road Accident First Response".
- Nav segments (Services/Accidents/Network + Hindi sub-labels) sit in a `rgba(255,255,255,.07)` track, border `rgba(255,255,255,.1)`, radius 10; active = white pill, navy text; inactive text `#B9C4D8`.
- Red "**Report Incident**" FAB pill bottom-right (padding 14×22, radius 99, CTA gradient, white "+" in a 20px translucent circle, pulse animation: box-shadow ring 2.6s infinite). Hidden while the report sheet is open.
- "Timeline" white pill bottom-left over the map (blue 8px dot + label).
- Map controls top-right: white rounded-10 stack of three 36×34 buttons (+ / − / N) separated by hairlines.

### 2. Services (map)
- Map is full-bleed under floating cards. **No InfoWindows open by default** — open on marker click, one at a time.
- **Clustered markers**: circular count badges (min 26px, category color, 2px white ring, 11/700 white text). Use `@googlemaps/markerclusterer`.
- **Default layers ON: Hospitals, Ambulance, Fire, Towing. OFF: Mechanics, Police, Pharmacies, Fuel.**
- Floating left panel: 342px wide, inset 14px from top/left/bottom, white, radius 14, border `#E4E1D8`.
  - Header row: "Emergency services" 13.5/600 + Hindi sub-line; 26px collapse button `‹` (border `#E4E1D8`, bg `#FAF9F5`). Collapsed → whole panel replaced by a floating "☰ Emergency services ›" button top-left.
  - Search input: full-width, 9px padding, bg `#FAF9F5`, radius 9, placeholder "Search hospitals, police, fuel…". Filters the facility list and map.
  - Caps label "SERVICE LAYERS · सेवा परतें", then one row per category: 10px color dot (35% opacity when off), name 13/500 + Hindi 11px sub-line, count badge (11px, `#F3F1EC` pill), iOS-style switch 34×20 (blue `#2456A6` on / `#D8D4C9` off, 16px white knob, 150ms). "sample" datasets show a tiny 9.5px saffron superscript tag. Row hover `#F6F4EE`.
  - Category colors: Hospitals `#2456A6` (160), Ambulance `#1E7F4F` (sample), Fire `#C6362C` (sample), Towing `#6B7280` (sample), Mechanics `#374151` (158), Police `#4F46E5` (136), Pharmacies `#0D9488` (159), Fuel `#B45309` (153).
  - Hairline divider, caps label "NEARBY FACILITIES · निकट सुविधाएँ", then rows: dot, name 13/500 ellipsized, meta 11.5px muted, distance right-aligned 12/600 blue. Click → pan/zoom map + open that marker's InfoWindow.

### 3. Accidents (map)
Same shell; panel title "Reported incidents · दर्ज घटनाएँ".
- Filter pills row: "◆ Road defects" (brown `#6B4226`) and "● Accidents" (red `#D14036`); toggleable — active = colored border + 8% tint bg + colored text; inactive = gray.
- Report cards (hover `#F6F4EE` + hairline border): severity chip (10/700, HIGH red-soft / MED saffron-soft / LOW gray), title 13/600, relative time 11px right; second line: location 12px ellipsized + status pill (Awaiting dispatch = red tint `#FBEAE8`/`#A32B22`, others `#F3F1EC`/`#8A8578`). Click → pan to marker.
- Markers: accidents = red `#D14036` circle count badges; road defects = brown `#6B4226` 45°-rotated rounded squares (diamonds).

### 4. Network (dashboard — same data, restyled)
Scroll page on `#F3F1EC`, content max-width 1060px centered, padding 26/24.
- Header row: title 19/700 "Signals DPG — Network Dashboard", sub 12.5 muted "Computed at … — cached rollup, not live"; right: "Export CSV" (white, blue text/border-hover) + "Refresh" buttons.
- Saffron-soft banner: bold "Local instance" + "Shows only what this console has published — not a live network feed."
- Two section cards ("Incidents (Control Room)", "Responder Facilities"): caps label; 3 stat tiles in a grid (`#FAF9F5` inset, radius 11 — number 24/700 over 11.5px label: 6/7/0 and 826/0/8); status-chip row (New / Dispatch active / Awaiting dispatch / Older — "hot" chips red-tinted); then record rows (hover `#F6F4EE`): 8px status dot (red = awaiting, gray = older, green = new) + name 13px + status pill right.

### 5. Report Incident sheet
Bottom sheet: centered, `min(880px, 96vw)`, max-height 82%, radius `18px 18px 0 0`, drag handle (44×4 `#E4E1D8`), backdrop `rgba(14,26,47,.4)` + `backdrop-filter: blur(2px)`, slide-up 280ms ease. Header: "Report Incident · घटना रिपोर्ट करें" + 30px ✕ button.
Five equal-width underline tabs (2.5px colored underline when active; SOS `#C6362C`, Pothole `#B06712`, others `#2456A6`; inactive `#8A8578`), each with Hindi sub-label:

1. **SOS** — two cards side-by-side (stack on mobile): "What SOS does" (red-soft, 4 bullets: requests GPS, creates high-priority incident, triggers severity assessment, appends to session log) and "What SOS does NOT do" (gray inset, 3 bullets: doesn't auto-call services, doesn't transmit externally in real time, dispatch is manual). Full-width CTA "SEND SOS · एसओएस भेजें" (17px padding, radius 13, CTA gradient, letter-spacing .08em).
2. **Text** — Incident type input ("Browse incident types…", auto-detects as you type); Location field (pattern below); Description textarea; "Vehicles involved" + "Casualties / injured" number inputs (90px); **Observed conditions as toggle chips** (Conscious, Breathing, Trapped, Heavy bleeding, Fire, Hazardous material — pill, blue border + `#EDF2FA` bg when selected) replacing the old full-width checkboxes; submit button.
3. **Speech-to-text** — language segmented control (English / हिंदी, in `#F3F1EC` track); 64px circular mic button (blue outline, `#EDF2FA` bg, line-icon mic); caption "Tap to start recording — transcript fills the form below"; then Location + Description (transcript-filled, editable); submit.
4. **Voice** — language segmented control; 84px circular navy-gradient mic button (white line-icon mic); "Start conversation · बातचीत शुरू करें"; explainer line; "Or set location manually on the map" link.
5. **Pothole** — saffron-soft info note ("Reporting a road defect — pin the location, describe, select severity; appears on the Accidents tab"); Location field; Description; severity segmented LOW/MEDIUM/HIGH (equal thirds, 1.5px borders; selected = filled green/saffron/red, white text); submit.

**Location field pattern (all modes):** button with a small map-pin line icon + label. Unset: `1.5px dashed #C9B98F`, bg `#FDFBF6`, muted text "Tap here, then tap map to set location". Set: solid green-soft (`#EAF5EE` / `#C4E2CF`), 600-weight `#175C3B` text with the coordinates. **Submit gating:** disabled state is gray `#EFECE4` with label "Set location to submit"; once location is set it becomes the red CTA gradient "Submit report · रिपोर्ट भेजें".

**Icons:** minimal line icons only (24×24 viewBox, `stroke: currentColor`, stroke-width 2, round caps/joins — feather-style: mic, map-pin, shield). No emoji anywhere.

### 6. Post-report view (same sheet, replaces form on submit)
Card stack, top → bottom:
1. Green success strip: 22px ✓ circle + "Incident `INC-…` created and logged" (ID in monospace).
2. **Severity assessment** card (border `#EED9B8`): saffron-soft header bar — caps title + "Operator selected" white pill. Body 2-col (`1fr 190px`): left = Incident type (14.5/600), Impact assessment, "Agencies to notify" chips (Ambulance green / Police blue / NHAI Maintenance saffron, tinted pills), "Ask next" as one inline numbered line. Right (hairline-divided) = 74px score ring (4px `#DD8A20` border, number 30/700), "MEDIUM" 13/700 `#B06712`, 4-segment bar (22×6: green, saffron, 2× gray), "Verify before acting" 10.5px.
3. **ETA** card (green-soft): caps "Estimated ambulance arrival"; "Ambulance inbound from Khanapara · 2 ambulances (BLS)"; "~34 min · 19.3 km — based on current road distance"; right: live `mm:ss` countdown 26/700 green tabular-nums + "MIN : SEC REMAINING"; 6px progress bar; disclaimer "Calculated estimate — not live tracking. We do not track vehicles."
4. **Matched hospitals**: caps header + right hint "traffic time · trauma · specialty". Ranked rows: 24px rank circle (rank 1 = navy filled; card gets 1.5px blue border + `#F7FAFE` bg), name 13.5/600 + saffron "Unverified" pill + one-line 12px note; right-aligned bold blue minutes over "km · current traffic". Data: Nemcare Hospital 19 min/7.6 km; Gauhati Medical College 24 min/13.0 km; Downtown Hospital 27 min/9.8 km. Green footnote: "✓ Drive times from Routes API — current traffic, vehicle leaving now. We do not track ambulances."
5. Two-up grid: **Nearest police station** card (Dispur Police Station, NH-27 circle, bold blue 54 min · 42.8 km, tel link 0361-2540222) + **Routes on map** legend (solid blue = hospital, dashed navy = police, dashed green = ambulance).
6. **Dispatch log** card: header ✓ "2 notifications logged — dispatched on severity assessment" + timestamp; one row per recipient: name + tinted "Hospital notified"/"Police notified" tag + green "✓ Sent"; sub-line "SMS / Push · time — awaiting acknowledgement from recipient · Show exact message text" (link). Gray inset footnote: the log doesn't confirm delivery; no "en route" status — no real-time link to responders.
7. Full-width navy Close button (radius 12, hover `#1C3152`).

## Interactions & Behavior
- Tab switching (header nav, sheet modes) — instant, no route reload needed for sheet modes.
- Panel collapse/expand; layer switches show/hide map layers; search filters list + markers.
- Marker click → single InfoWindow; list row click → pan/zoom + open InfoWindow.
- FAB opens sheet (slide-up 280ms); backdrop or ✕ closes and resets to SOS mode.
- Submit gating on location; SEND SOS / Submit → post-report view in the same sheet.
- ETA countdown ticks every second (tabular numerals, no layout shift).
- Hovers: rows `#F6F4EE`; buttons brighten ~6% or lift 1px. Switch/knob 150ms ease.
- All existing logic unchanged: severity auto-assessment, Routes API drive times, dispatch notifications, EN/Hindi speech recognition, session event log, SOS GPS request.

## State Management (UI-level only — reuse existing app state)
`activeTab` (services|accidents|network), `panelOpen`, per-category layer toggles, accident filter toggles (defects/accidents), `sheetOpen`, `reportMode` (sos|text|speech|voice|pothole), `locationSet` + coords, observed-condition selections, pothole severity, `submitted`, ETA seconds remaining, `language` (en|hi).

## Responsive (mobile)
- Header: hide corridor subtitle; nav becomes a compact 3-segment bar under the header or a bottom tab bar.
- Left panel → bottom drawer (peek header, drag to expand); map stays visible behind.
- Report sheet: full-width; SOS cards and post-report two-up grids stack to one column; mode tabs scroll horizontally if needed.
- Hit targets ≥ 44px.

## Assets
- Google Fonts: IBM Plex Sans, Noto Sans Devanagari.
- Icons: feather-style inline SVGs in the design file (shield brand mark, mic, map-pin). Reuse an equivalent icon set already in the codebase (e.g. lucide/feather) — do not use emoji.
- Map: Google Maps JS (existing). Marker clustering via `@googlemaps/markerclusterer`.

## Files
- `Transport Sahayak Redesign.dc.html` — the interactive hi-fi design (all screens/states; open in a browser, keep `support.js` next to it).
- `support.js` — runtime for the design file (reference only, not for production).
