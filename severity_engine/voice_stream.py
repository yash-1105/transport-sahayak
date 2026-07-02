"""
voice_stream.py — Google Cloud Speech-to-Text V2 (Chirp) streaming bridge.

Consumes an async stream of raw PCM16/16kHz/mono audio chunks (from a FastAPI
WebSocket — see the /ws/voice route in app.py) and forwards them to Speech-to-
Text V2's bidi StreamingRecognize API, yielding interim/final transcript
events as they arrive. No V1 API, no browser Web Speech API involved.

Credentials — checked in this order, first match wins:
  1. GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 — base64-encoded service account JSON,
     set as a Railway secret in production.
  2. GOOGLE_SERVICE_ACCOUNT_JSON — raw service account JSON string (same idea,
     unencoded — supported in case base64 is inconvenient in a given host).
  3. A local file (default: ~/Downloads/trans-sahayak-8f5e1c61e87e.json,
     overridable via GOOGLE_SERVICE_ACCOUNT_LOCAL_PATH), for local dev only —
     this path is never present in production, so it's skipped there.
Deliberately does NOT fall back to bare `gcloud auth login` / ambient ADC
discovery, and never reads a Google API key — this makes the failure mode an
explicit, loud error instead of a silent "worked on my machine, not in prod"
surprise (the project already had one Google credentials debugging saga this
week over the severity engine's Gemini key).
"""
import base64
import json
import logging
import os
from typing import AsyncIterator, Optional

from google.api_core.client_options import ClientOptions
from google.cloud.speech_v2 import SpeechAsyncClient
from google.cloud.speech_v2.types import cloud_speech
from google.oauth2 import service_account

logger = logging.getLogger("voice_stream")

_LOCAL_CREDENTIALS_PATH = os.path.expanduser(
    os.environ.get("GOOGLE_SERVICE_ACCOUNT_LOCAL_PATH", "~/Downloads/trans-sahayak-8f5e1c61e87e.json")
)
_LOCATION = os.environ.get("GOOGLE_SPEECH_LOCATION", "us-central1")
_MODEL = os.environ.get("GOOGLE_SPEECH_MODEL", "chirp_2")  # latest Chirp generation
_LANGUAGE_CODES = ["en-IN", "hi-IN"]  # English + Hindi only, per requirements

_SAMPLE_RATE_HZ = 16000
_AUDIO_CHANNELS = 1


class SpeechCredentialsError(RuntimeError):
    """Raised when no usable Google Cloud credentials can be located."""


def _load_service_account_info() -> Optional[dict]:
    b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64")
    if b64:
        try:
            info = json.loads(base64.b64decode(b64))
            logger.info("Loaded Speech-to-Text credentials from GOOGLE_SERVICE_ACCOUNT_JSON_BASE64")
            return info
        except Exception:
            logger.exception("Failed to decode/parse GOOGLE_SERVICE_ACCOUNT_JSON_BASE64")

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        try:
            info = json.loads(raw)
            logger.info("Loaded Speech-to-Text credentials from GOOGLE_SERVICE_ACCOUNT_JSON")
            return info
        except Exception:
            logger.exception("Failed to parse GOOGLE_SERVICE_ACCOUNT_JSON")

    if os.path.exists(_LOCAL_CREDENTIALS_PATH):
        try:
            with open(_LOCAL_CREDENTIALS_PATH, encoding="utf-8") as f:
                info = json.load(f)
            logger.info("Loaded Speech-to-Text credentials from local file %s", _LOCAL_CREDENTIALS_PATH)
            return info
        except Exception:
            logger.exception("Failed to read local credentials file %s", _LOCAL_CREDENTIALS_PATH)

    return None


# Lazily-initialised, cached at module scope — credential loading/client
# construction happens once, not per WebSocket connection.
_project_id: Optional[str] = None
_client: Optional[SpeechAsyncClient] = None


def _get_client() -> SpeechAsyncClient:
    global _project_id, _client
    if _client is not None:
        return _client

    info = _load_service_account_info()
    if not info:
        raise SpeechCredentialsError(
            "No Google Cloud Speech credentials found. Set GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 "
            "or GOOGLE_SERVICE_ACCOUNT_JSON (Railway/production), or place the local service "
            f"account file at {_LOCAL_CREDENTIALS_PATH} (local dev)."
        )
    project_id = info.get("project_id")
    if not project_id:
        raise SpeechCredentialsError("Service account JSON has no project_id field.")

    credentials = service_account.Credentials.from_service_account_info(info)
    # Regional models (Chirp included) require the matching regional API
    # endpoint -- the default global endpoint only accepts recognizer paths
    # under locations/global, and rejects a regional location like us-central1
    # with a 400 (confirmed while testing this integration).
    client_options = ClientOptions(api_endpoint=f"{_LOCATION}-speech.googleapis.com")
    _client = SpeechAsyncClient(credentials=credentials, client_options=client_options)
    _project_id = project_id
    logger.info("Speech-to-Text V2 client initialised for project %s (model=%s, location=%s)",
                project_id, _MODEL, _LOCATION)
    return _client


def _recognizer_path() -> str:
    """The implicit/default recognizer ('_') — no need to pre-create a named
    recognizer resource for ad-hoc streaming recognition."""
    if _project_id is None:
        _get_client()
    return f"projects/{_project_id}/locations/{_LOCATION}/recognizers/_"


def _streaming_config() -> cloud_speech.StreamingRecognitionConfig:
    recognition_config = cloud_speech.RecognitionConfig(
        explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
            encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=_SAMPLE_RATE_HZ,
            audio_channel_count=_AUDIO_CHANNELS,
        ),
        language_codes=_LANGUAGE_CODES,
        model=_MODEL,
        features=cloud_speech.RecognitionFeatures(
            enable_automatic_punctuation=True,
        ),
    )
    return cloud_speech.StreamingRecognitionConfig(
        config=recognition_config,
        streaming_features=cloud_speech.StreamingRecognitionFeatures(
            interim_results=True,
        ),
    )


async def _request_generator(
    audio_chunks: AsyncIterator[bytes],
) -> AsyncIterator[cloud_speech.StreamingRecognizeRequest]:
    """The first request on a StreamingRecognize call must carry only the
    config (recognizer + streaming_config); every request after that carries
    a raw audio chunk. This framing is required by the API, not optional."""
    yield cloud_speech.StreamingRecognizeRequest(
        recognizer=_recognizer_path(),
        streaming_config=_streaming_config(),
    )
    chunk_count = 0
    async for chunk in audio_chunks:
        if not chunk:
            continue
        chunk_count += 1
        yield cloud_speech.StreamingRecognizeRequest(audio=chunk)
    logger.info("Forwarded %d audio chunk(s) to Speech-to-Text", chunk_count)


async def stream_transcripts(audio_chunks: AsyncIterator[bytes]) -> AsyncIterator[dict]:
    """
    Consumes an async stream of raw PCM16/16kHz/mono audio chunks, forwards
    them to Speech-to-Text V2 StreamingRecognize, and yields
    {"type": "interim" | "final", "text": str} events as they arrive.

    Raises SpeechCredentialsError (no/invalid credentials) or
    google.api_core.exceptions.GoogleAPIError (API-level failure, quota,
    network) — callers must catch both and report a clean error to the
    client rather than letting the exception propagate raw. An empty
    recording (audio_chunks yields nothing) simply produces zero events and
    returns normally — not an error.
    """
    client = _get_client()
    responses = await client.streaming_recognize(requests=_request_generator(audio_chunks))
    async for response in responses:
        for result in response.results:
            if not result.alternatives:
                continue
            text = result.alternatives[0].transcript
            if not text:
                continue
            yield {"type": "final" if result.is_final else "interim", "text": text}
