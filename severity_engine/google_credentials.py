"""
google_credentials.py — shared Google Cloud service-account credential loading.

Extracted from voice_stream.py's identical logic so severity_engine/dispatcher_live.py
(Gemini Live / Vertex AI) can reuse the exact same credential source as the
Speech-to-Text V2 bridge, without touching voice_stream.py itself (its working
Chirp pipeline is left byte-for-byte untouched by design).

Credentials — checked in this order, first match wins:
  1. GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 — base64-encoded service account JSON,
     set as a Railway secret in production.
  2. GOOGLE_SERVICE_ACCOUNT_JSON — raw service account JSON string.
  3. A local file (default: ~/Downloads/trans-sahayak-8f5e1c61e87e.json,
     overridable via GOOGLE_SERVICE_ACCOUNT_LOCAL_PATH), for local dev only.
No ADC/`gcloud auth login` fallback and no API key path — an explicit, loud
error instead of a silent "works on my machine, not in prod" surprise.
"""
import base64
import json
import logging
import os
from typing import Optional

logger = logging.getLogger("google_credentials")

_LOCAL_CREDENTIALS_PATH = os.path.expanduser(
    os.environ.get("GOOGLE_SERVICE_ACCOUNT_LOCAL_PATH", "~/Downloads/trans-sahayak-8f5e1c61e87e.json")
)


def load_service_account_info() -> Optional[dict]:
    b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64")
    if b64:
        try:
            info = json.loads(base64.b64decode(b64))
            logger.info("Loaded Google Cloud credentials from GOOGLE_SERVICE_ACCOUNT_JSON_BASE64")
            return info
        except Exception:
            logger.exception("Failed to decode/parse GOOGLE_SERVICE_ACCOUNT_JSON_BASE64")

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        try:
            info = json.loads(raw)
            logger.info("Loaded Google Cloud credentials from GOOGLE_SERVICE_ACCOUNT_JSON")
            return info
        except Exception:
            logger.exception("Failed to parse GOOGLE_SERVICE_ACCOUNT_JSON")

    if os.path.exists(_LOCAL_CREDENTIALS_PATH):
        try:
            with open(_LOCAL_CREDENTIALS_PATH, encoding="utf-8") as f:
                info = json.load(f)
            logger.info("Loaded Google Cloud credentials from local file %s", _LOCAL_CREDENTIALS_PATH)
            return info
        except Exception:
            logger.exception("Failed to read local credentials file %s", _LOCAL_CREDENTIALS_PATH)

    return None
