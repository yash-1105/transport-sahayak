"""Centralised configuration + startup validation for the Exotel integration.

All environment reads for the Exotel layer live here, so the runtime, the startup
validation, and the /exotel/health endpoint all agree on one source of truth.

Nothing here is "required" in the sense of crashing the backend if absent — the
whole integration is off unless EXOTEL_ENABLED is true. When it IS enabled,
`validate()` is called at mount time (startup): a HARD misconfiguration (bad WS
path, non-numeric/zero sample rate, malformed APP_BASE_URL, nonsensical HTTP
settings) raises `ExotelConfigError` so the endpoint is NOT mounted (the existing
browser service stays up, loudly logged), while SOFT issues (localhost base URL in
what looks like prod, missing Sarvam/Google creds the Hindi pipeline needs) are
logged as warnings only.
"""
from __future__ import annotations

import os

from .logging_utils import get_logger

logger = get_logger("exotel.config")


class ExotelConfigError(RuntimeError):
    """A hard Exotel misconfiguration — the endpoint must not be mounted."""


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# ── transport ─────────────────────────────────────────────────────────────────
EXOTEL_ENABLED = _flag("EXOTEL_ENABLED", False)
EXOTEL_DEBUG = _flag("EXOTEL_DEBUG", False)  # verbose per-event/latency logging (no PII/audio)
EXOTEL_WS_PATH = (os.environ.get("EXOTEL_WS_PATH", "/exotel/ws").strip() or "/exotel/ws")
EXOTEL_HEALTH_PATH = (os.environ.get("EXOTEL_HEALTH_PATH", "/exotel/health").strip() or "/exotel/health")
# Exotel AgentStream's DEFAULT stream rate is 8 kHz PCM16 mono (also supports
# 16000/24000). The actual rate is read from the `start` frame's media_format and
# the resampler auto-reconfigures — this is only the pre-start default.
_RAW_SAMPLE_RATE = os.environ.get("EXOTEL_SAMPLE_RATE", "8000")

# ── services reuse (the app's own endpoints) ──────────────────────────────────
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:3000").rstrip("/")
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org").rstrip("/")

# ── HTTP resilience for every external call in services.py ────────────────────
HTTP_TIMEOUT = float(os.environ.get("EXOTEL_HTTP_TIMEOUT", "8") or "8")   # seconds, per attempt
HTTP_RETRIES = int(os.environ.get("EXOTEL_HTTP_RETRIES", "2") or "2")     # extra attempts after the first
# backoff (seconds) between attempts; kept short because some calls run inside a
# live turn. Index clamps to the last entry for any further retries.
HTTP_BACKOFF = (0.3, 0.8, 1.5)


def dbg(log, msg: str, *args) -> None:
    """Emit a verbose diagnostic line only when EXOTEL_DEBUG is on (at INFO level,
    so it shows without changing the global log level). Callers must never pass raw
    audio bytes, transcript text, or full phone numbers — sizes/rates/counts only."""
    if EXOTEL_DEBUG:
        log.info("[debug] " + msg, *args)


def sample_rate() -> int:
    """Parsed EXOTEL_SAMPLE_RATE, validated. Raises ExotelConfigError if invalid."""
    try:
        r = int(_RAW_SAMPLE_RATE)
    except (TypeError, ValueError):
        raise ExotelConfigError(f"EXOTEL_SAMPLE_RATE must be an integer, got {_RAW_SAMPLE_RATE!r}")
    if r <= 0:
        raise ExotelConfigError(f"EXOTEL_SAMPLE_RATE must be a positive integer, got {r}")
    return r


def check() -> tuple[list[str], list[str]]:
    """Non-raising inspection used by /exotel/health. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    if not EXOTEL_WS_PATH.startswith("/"):
        errors.append(f"EXOTEL_WS_PATH must start with '/', got {EXOTEL_WS_PATH!r}")
    if not EXOTEL_HEALTH_PATH.startswith("/"):
        errors.append(f"EXOTEL_HEALTH_PATH must start with '/', got {EXOTEL_HEALTH_PATH!r}")
    try:
        sample_rate()
    except ExotelConfigError as e:
        errors.append(str(e))
    if not APP_BASE_URL.startswith(("http://", "https://")):
        errors.append(f"APP_BASE_URL must be an http(s) URL, got {APP_BASE_URL!r}")
    if HTTP_TIMEOUT <= 0:
        errors.append(f"EXOTEL_HTTP_TIMEOUT must be > 0, got {HTTP_TIMEOUT}")
    if HTTP_RETRIES < 0:
        errors.append(f"EXOTEL_HTTP_RETRIES must be >= 0, got {HTTP_RETRIES}")

    if APP_BASE_URL.startswith(("http://", "https://")) and (
        "localhost" in APP_BASE_URL or "127.0.0.1" in APP_BASE_URL
    ):
        warnings.append(
            f"APP_BASE_URL={APP_BASE_URL} looks local — phone-call responder/ETA/complaint "
            "lookups will hit this, not the deployed app.")
    if not os.environ.get("SARVAM_API_KEY"):
        warnings.append("SARVAM_API_KEY is not set — the Hindi STT/TTS pipeline Exotel drives will fail at call time.")
    if not (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64") or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")):
        warnings.append("No GOOGLE_SERVICE_ACCOUNT_JSON[_BASE64] set — Gemini reasoning for Hindi calls will fail at call time (unless a local dev creds file is present).")
    return errors, warnings


def validate() -> None:
    """Startup gate. Raises ExotelConfigError on any hard error; logs soft warnings.
    Called from websocket.register() only when EXOTEL_ENABLED is true."""
    errors, warnings = check()
    for w in warnings:
        logger.warning("Exotel config: %s", w)
    if errors:
        raise ExotelConfigError("; ".join(errors))


def summary() -> dict:
    """Config snapshot for /exotel/health (no secrets)."""
    return {
        "enabled": EXOTEL_ENABLED,
        "debug": EXOTEL_DEBUG,
        "ws_path": EXOTEL_WS_PATH,
        "health_path": EXOTEL_HEALTH_PATH,
        "sample_rate": _RAW_SAMPLE_RATE,
        "app_base_url": APP_BASE_URL,
        "http_timeout_s": HTTP_TIMEOUT,
        "http_retries": HTTP_RETRIES,
    }
