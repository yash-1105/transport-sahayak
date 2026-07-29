# Prompt for Claude Code

Copy everything below the line into Claude Code, run from the Transport Sahayak repo root, with the `design_handoff_ui_redesign/` folder placed in the repo (or give its path).

---

I'm redesigning the UI of this app (Transport Sahayak — road-accident first-response, deployed at transport-sahayak.vercel.app). The folder `design_handoff_ui_redesign/` contains the complete design handoff:

- `README.md` — the full spec: design tokens, per-screen layout, exact colors/typography/spacing, interactions, and responsive rules. Treat it as the source of truth.
- `Transport Sahayak Redesign.dc.html` + `support.js` — an interactive HTML prototype of the target design (open in a browser to inspect any state). It is a design reference only — recreate it with this codebase's existing framework, components, and patterns; do not copy its code or runtime.

STRICT CONSTRAINT — visual/layout changes only. Do not change any functionality, business logic, data flows, API calls, state semantics, or copy meaning. Everything that works today must work identically after: severity auto-assessment, Routes API drive times, dispatch notifications, EN/Hindi speech recognition and voice modes, SOS GPS flow, session event log, timeline, language toggle, all counts and data sources.

Implement in this order:
1. Read `design_handoff_ui_redesign/README.md` fully, then explore the codebase to map each existing screen/component to the spec before editing.
2. Global: design tokens (colors, IBM Plex Sans + Noto Sans Devanagari, radii, shadows), 60px navy header with shield brand mark, segmented nav, EN/हिं toggle, red pulsing Report Incident FAB.
3. Services map: remove all default-open popups (InfoWindow only on marker click, one at a time); cluster markers with @googlemaps/markerclusterer; default only Hospitals/Ambulance/Fire/Towing layers ON; replace the horizontal chip row with the 342px collapsible floating left panel (search, layer switches with counts, nearby-facilities list).
4. Accidents tab: same panel shell with defect/accident filter pills and the report-card list; red circle / brown diamond markers.
5. Network tab: restyle the dashboard per spec (stat tiles, status chips, record rows) — same data. Also enable/unhide the Network tab in the deployed build (it currently only shows locally).
6. Report Incident bottom sheet: keep all 5 modes as tabs (SOS, Text, Speech-to-text, Voice, Pothole) restyled per spec; location-first submit gating; observed conditions as toggle chips; minimal line icons (no emoji).
7. Post-report view: restructure into the card hierarchy — success strip → severity assessment → live ETA countdown → matched hospitals → police + routes legend → dispatch log → Close.
8. Responsive: apply the mobile rules in the README (panel becomes bottom drawer, sheet full-width, stacked grids, ≥44px hit targets).

Keep every existing label's meaning; add the bilingual Hindi sub-labels shown in the spec. After each major step, build and verify nothing functional regressed before moving on.
