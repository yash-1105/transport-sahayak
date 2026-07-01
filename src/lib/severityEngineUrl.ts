// Normalizes SEVERITY_ENGINE_URL so a common misconfiguration (pasting the bare
// host without a scheme, e.g. a Railway domain copied without "https://") fails
// loudly and correctly rather than silently as an unhandled fetch() TypeError.

export function getSeverityEngineUrl(): string {
  const raw = (process.env.SEVERITY_ENGINE_URL ?? "http://localhost:8000").trim();
  const withScheme = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
  return withScheme.replace(/\/+$/, "");
}
