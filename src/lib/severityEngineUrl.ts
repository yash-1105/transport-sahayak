// Normalizes SEVERITY_ENGINE_URL so a common misconfiguration (pasting the bare
// host without a scheme, e.g. a Railway domain copied without "https://") fails
// loudly and correctly rather than silently as an unhandled fetch() TypeError.

export function getSeverityEngineUrl(): string {
  const raw = (process.env.SEVERITY_ENGINE_URL ?? "http://localhost:8000").trim();
  const withScheme = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
  return withScheme.replace(/\/+$/, "");
}

// Unwraps fetch()'s generic "TypeError: fetch failed" to surface the actual
// underlying cause (DNS failure, connection refused, TLS error, etc.) — the
// bare message alone isn't enough to diagnose a misconfigured engine URL.
export function describeFetchError(e: unknown): string {
  if (e instanceof Error) {
    const cause = (e as { cause?: unknown }).cause;
    const causeStr = cause instanceof Error ? cause.message : cause ? String(cause) : null;
    return causeStr ? `${e.message} (cause: ${causeStr})` : e.message;
  }
  return String(e);
}
