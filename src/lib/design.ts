// UI Redesign design tokens — TS mirror of the CSS custom properties in
// globals.css, for use in inline styles that need JavaScript values (marker
// clusterer renderers, dynamic style objects, etc.). Keep in sync with the
// :root block in globals.css and the design handoff README.

export const C = {
  navy900: "#0E1A2F",
  navy800: "#14243E",
  navy700: "#173B77",
  blue: "#2456A6",
  red: "#C6362C",
  saffron: "#DD8A20",
  green: "#1E7F4F",

  // Soft tints
  redSoftBg: "#FBEAE8",
  redSoftBorder: "#EFC7C3",
  redSoftText: "#A32B22",
  saffronSoftBg: "#FBF1E4",
  saffronSoftBorder: "#EED9B8",
  saffronSoftText: "#8A5A17",
  greenSoftBg: "#EAF5EE",
  greenSoftBorder: "#C4E2CF",
  greenSoftText: "#175C3B",
  blueSoftBg: "#EDF2FA",
  blueSoftBorder: "#C7D4EC",
  blueSoftText: "#173B77",

  // Surfaces
  page: "#F3F1EC",
  card: "#FFFFFF",
  inset: "#FAF9F5",
  border: "#E4E1D8",
  hairline: "#EFECE4",

  // Text
  ink: "#1C2434",
  body: "#3B4658",
  secondary: "#5B6572",
  muted: "#8A8578",
  faint: "#B4AFA2",
  onNavySub: "#7D8DA9",
  navInactive: "#B9C4D8",
} as const;

// Brand gradient (saffron → red) used on the header brand tile and CTA buttons.
export const BRAND_GRADIENT = "linear-gradient(135deg,#E8862B,#C6362C)";
export const CTA_GRADIENT = "linear-gradient(135deg,#D14036,#B92C22)";
export const HEADER_GRADIENT = "linear-gradient(180deg,#14243E,#0E1A2F)";

export const RADIUS = {
  card: 14,
  sheet: 18,
  input: 10,
  row: 9,
  pill: 99,
} as const;

export const SHADOW = {
  panel: "0 8px 28px rgba(14,26,47,.12)",
  sheet: "0 -12px 48px rgba(14,26,47,.3)",
  fab: "0 8px 24px rgba(198,54,44,.4)",
  mapControl: "0 2px 8px rgba(28,36,52,.1)",
  floatBtn: "0 4px 14px rgba(14,26,47,.12)",
} as const;
