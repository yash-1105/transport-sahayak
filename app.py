"""
app.py — FastAPI wrapper around the rule-first severity engine.

Run locally (for tomorrow's demo):
    pip install -r requirements.txt
    uvicorn app:app --reload --port 8000

Your existing Next.js /api/assess route calls POST http://localhost:8000/assess.
No new deployment required — this runs alongside your POC on localhost.
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional


def _load_env_file(path: Path) -> None:
    """Minimal, dependency-free .env loader for local `uvicorn app:app` runs.

    python-dotenv is NOT a project dependency, and uvicorn does not read .env on
    its own, so without this the backend process only ever saw whatever env vars
    were exported in its launch shell -- meaning SARVAM_API_KEY (and the other
    SARVAM_TTS_*/GEMINI_* knobs) sitting in the repo-root .env were invisible,
    and the Hindi dispatcher reported "not configured on the server" even though
    the key was right there in .env. (English kept working because its Google
    credentials fall back to the on-disk service-account file and everything
    else has a default.) CLAUDE.md documents .env as the intended local-uvicorn
    config source, so this just makes that real.

    Only sets keys NOT already present in os.environ, so a real exported env var
    always wins; and it's a no-op in production (Railway sets real env vars and
    ships no .env file). Parses simple KEY=VALUE lines only -- fine for the
    single-line keys here; multi-line JSON blobs are not supported (this project
    supplies Google creds via a file or the *_BASE64 single-line var instead)."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# Load .env BEFORE importing severity_engine, so its module-level os.environ.get
# reads (Gemini/Sarvam model names, TTS knobs, etc.) also see these values.
_ROOT = Path(__file__).resolve().parent
_load_env_file(_ROOT / ".env")
_load_env_file(_ROOT / ".env.local")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.api_core.exceptions import GoogleAPIError
from google.genai.errors import APIError as GeminiLiveAPIError
from pydantic import BaseModel

from severity_engine import engine
from severity_engine.classifier import (
    INDEX,
    _CATEGORY_MAP,
    get_categories as _get_categories,
    get_subtypes_for as _get_subtypes_for,
    guess as _clf_guess,
)
from severity_engine.dispatcher_live import (
    DispatcherCredentialsError,
    DispatcherSession,
)
from severity_engine.dispatcher_live import SUPPORTED_LANGUAGES as DISPATCHER_LANGUAGES
from severity_engine.dispatcher_hindi import HindiDispatcherSession
from severity_engine.sarvam_speech import SarvamCredentialsError
from severity_engine.voice_stream import (
    SUPPORTED_LANGUAGES,
    SpeechCredentialsError,
    stream_transcripts,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("app")

app = FastAPI(title="Transport Sahayak — Rule-First Severity Engine", version="1.0")

# allow the local Next.js dev server to call this during the demo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Signals(BaseModel):
    casualties: int = 0
    fatalities: int = 0
    fire: bool = False
    hazmat: bool = False
    entrapment: bool = False
    roadBlocked: bool = False
    vulnerableVictim: bool = False
    vehiclesInvolved: int = 1


class Location(BaseModel):
    km: Optional[float] = None
    latlng: Optional[list] = None


class Incident(BaseModel):
    subType: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = "en"


class AssessRequest(BaseModel):
    incident: Incident
    signals: Signals = Signals()
    location: Optional[Location] = None


@app.get("/health")
def health():
    return {"ok": True, "records": len(INDEX)}


@app.get("/debug/gemini")
def debug_gemini():
    """
    Diagnostic only — surfaces the REAL error from a live Gemini call (bad key,
    quota, billing not enabled, etc.) instead of the silent None the actual
    assess() pipeline returns on failure by design. Safe to leave deployed:
    makes one trivial, cheap call, never touches incident data.
    """
    from severity_engine.gemini_client import gemini_health_check
    ok, detail = gemini_health_check()
    return {"ok": ok, "detail": detail}


@app.get("/subtypes")
def subtypes():
    """Every sub-type with its curated category (deduped by subType string)."""
    seen: set[str] = set()
    result = []
    for r in INDEX:
        st = r["subType"]
        if st not in seen:
            seen.add(st)
            result.append({"category": _CATEGORY_MAP.get(st, "Other"), "subType": st})
    return result


@app.get("/categories")
def categories():
    """Return [{category, count}] sorted descending by count."""
    return _get_categories()


@app.get("/categories/subtypes")
def category_subtypes(name: str):
    """Return sorted list of subType strings in this category (name as query param to avoid slash routing issues)."""
    return _get_subtypes_for(name)


class GuessRequest(BaseModel):
    description: str


@app.post("/guess")
def guess_endpoint(req: GuessRequest):
    """Classify a free-text description; returns a confirm-card payload."""
    return _clf_guess(req.description)


@app.post("/assess")
def assess(req: AssessRequest):
    return engine.assess(
        req.incident.model_dump(),
        req.signals.model_dump(),
        req.location.model_dump() if req.location else None,
    )


async def _safe_send_json(websocket: WebSocket, payload: dict) -> None:
    """Best-effort send — the socket may already be closing/closed by the
    time an error is ready to report; never let that raise a second error."""
    try:
        await websocket.send_json(payload)
    except Exception:
        logger.debug("Could not send message on /ws/voice (socket likely closed)", exc_info=True)


@app.websocket("/ws/voice")
async def voice_stream_ws(websocket: WebSocket) -> None:
    """
    Streaming speech-to-text over WebSocket, backed by Google Cloud
    Speech-to-Text V2 (Chirp) — see severity_engine/voice_stream.py.

    Client protocol: connect with ?locale=en-IN or ?locale=hi-IN (defaults to
    en-IN if omitted/unrecognized — English and Hindi are the only two
    supported languages; Chirp 2 doesn't support recognizing both at once in
    a single request on this project, see voice_stream.py, so the reporter's
    selected language applies to the whole recording session). After the
    handshake, send raw PCM16/16kHz/mono audio as binary WebSocket frames, in
    small chunks, for as long as recording is active; send any text frame
    (the client sends "__end__") to signal "no more audio" WITHOUT closing
    the socket — the server needs a beat to flush the last transcript back
    before the connection goes away, so it closes the socket itself once
    streaming is done. Abruptly closing from the client works too (handled
    as a normal disconnect) but risks losing an in-flight final result — this
    raced and silently dropped the last utterance during testing, hence the
    explicit end-of-audio signal instead of relying on close alone. Server
    sends JSON text frames back: {"type": "interim", "text": "..."},
    {"type": "final", "text": "..."}, or {"type": "error", "message": "..."}.
    """
    await websocket.accept()
    language_code = websocket.query_params.get("locale", "")
    if language_code not in SUPPORTED_LANGUAGES:
        language_code = "en-IN"
    logger.info("Client connected to /ws/voice (language=%s)", language_code)

    audio_queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()
    chunks_received = 0

    async def receive_loop() -> None:
        nonlocal chunks_received
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    chunks_received += 1
                    await audio_queue.put(message["bytes"])
                elif message.get("text") is not None:
                    # any text frame means "no more audio" — end the request
                    # stream so Speech-to-Text can finalize and we can flush
                    # the last result back before the socket closes.
                    logger.info("Received end-of-audio signal on /ws/voice")
                    break
        except WebSocketDisconnect:
            logger.info("Client disconnected from /ws/voice (%d chunk(s) received)", chunks_received)
        except Exception:
            logger.exception("Error receiving audio on /ws/voice")
        finally:
            await audio_queue.put(None)  # sentinel: tells audio_iterator to stop

    async def audio_iterator():
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                break
            yield chunk

    receive_task = asyncio.create_task(receive_loop())

    try:
        async for event in stream_transcripts(audio_iterator(), language_code=language_code):
            await _safe_send_json(websocket, event)
    except SpeechCredentialsError as e:
        logger.error("Speech-to-Text credentials error: %s", e)
        await _safe_send_json(
            websocket, {"type": "error", "message": "Speech recognition is not configured on the server."}
        )
    except GoogleAPIError as e:
        logger.exception("Google Speech-to-Text API error")
        await _safe_send_json(websocket, {"type": "error", "message": f"Speech recognition failed: {e}"})
    except WebSocketDisconnect:
        logger.info("Client disconnected from /ws/voice mid-stream")
    except Exception:
        logger.exception("Unexpected error on /ws/voice")
        await _safe_send_json(
            websocket, {"type": "error", "message": "Unexpected server error during speech recognition."}
        )
    finally:
        receive_task.cancel()
        if chunks_received == 0:
            logger.info("No audio received on /ws/voice before the stream ended (empty recording)")
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/dispatcher")
async def dispatcher_ws(websocket: WebSocket) -> None:
    """
    Conversational voice dispatcher over WebSocket.

    English (en-IN) is backed by Gemini Live via Vertex AI — see
    severity_engine/dispatcher_live.py (unchanged). Hindi (hi-IN) is backed by
    Sarvam Saaras v3 (STT) + text Gemini reasoning + Sarvam Bulbul v3 (TTS) —
    see severity_engine/dispatcher_hindi.py. Both speak the exact same client
    protocol described below.

    Client protocol: connect with ?locale=en-IN or ?locale=hi-IN (defaults to
    en-IN). Send raw PCM16/16kHz/mono audio as binary frames while the caller
    is speaking, and JSON text frames for control messages:
    {"type":"location_result"|"location_error", "requestId":..., ...} in
    response to a server "request_location" message, or {"type":"end"} to end
    the call. Server sends binary PCM16/24kHz/mono synthesized speech back,
    plus JSON text frames: {"type":"ready"}, {"type":"status","state":...},
    {"type":"form_update","field":...,"value":...},
    {"type":"request_location","requestId":...},
    {"type":"submitted","incident":{...}}, {"type":"turn_complete"},
    {"type":"interrupted"}, {"type":"transcript",...} (internal, not
    rendered), or {"type":"error","message":...}.
    """
    await websocket.accept()
    language_code = websocket.query_params.get("locale", "")
    if language_code not in DISPATCHER_LANGUAGES:
        language_code = "en-IN"
    logger.info("Client connected to /ws/dispatcher (language=%s)", language_code)

    session = (
        HindiDispatcherSession(websocket)
        if language_code == "hi-IN"
        else DispatcherSession(websocket, language_code)
    )
    try:
        await session.run()
    except SarvamCredentialsError as e:
        logger.error("Sarvam credentials error: %s", e)
        await _safe_send_json(
            websocket, {"type": "error", "message": "The Hindi voice dispatcher is not configured on the server."}
        )
    except DispatcherCredentialsError as e:
        logger.error("Gemini credentials error: %s", e)
        await _safe_send_json(
            websocket, {"type": "error", "message": "The voice dispatcher is not configured on the server."}
        )
    except (GeminiLiveAPIError, GoogleAPIError) as e:
        logger.exception("Gemini Live API error")
        await _safe_send_json(websocket, {"type": "error", "message": f"Voice dispatcher failed: {e}"})
    except WebSocketDisconnect:
        logger.info("Client disconnected from /ws/dispatcher")
    except Exception:
        logger.exception("Unexpected error on /ws/dispatcher")
        await _safe_send_json(
            websocket, {"type": "error", "message": "Unexpected server error in the voice dispatcher."}
        )
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ── Exotel AgentStream (telephony transport, Hindi-only) ──────────────────────
# WHY this is the ONLY edit to app.py: this module owns the FastAPI `app` and
# EVERY WebSocket route registration, so a new endpoint must be mounted here —
# there is no other registration point. It is purely additive, guarded by
# EXOTEL_ENABLED (off by default => zero effect on the existing browser service),
# and touches no existing route. All Exotel code lives under integrations/exotel/.
try:
    from integrations.exotel.websocket import register as _register_exotel
    _register_exotel(app)
except Exception:
    logger.exception("Failed to mount the Exotel integration (existing service left unaffected)")
