"""The Exotel AgentStream endpoints: /exotel/ws + /exotel/health (configurable).

`/exotel/ws` accepts an Exotel Voicebot bidirectional-stream WebSocket, wraps it in
the ExotelWebSocketAdapter, and runs the SAME HindiDispatcherSession the browser
uses (via the thin ExotelHindiSession subclass). No business logic here — it's
transport wiring + robust teardown so the call always ends cleanly.

`/exotel/health` is a lightweight read-only status probe (always mounted, even when
the integration is disabled) reporting enabled/config/validation state.

Mounted from app.py via register(app). The WS endpoint mounts only when
EXOTEL_ENABLED is true AND config validation passes, so it has ZERO effect on the
existing service when Exotel isn't turned on or is misconfigured.
"""
from __future__ import annotations

import uuid

from fastapi import WebSocket, WebSocketDisconnect

from . import config
from .config import ExotelConfigError
from .logging_utils import get_logger, set_call_id
from .session import ExotelWebSocketAdapter, make_exotel_session

logger = get_logger("exotel.ws")


def health_payload() -> dict:
    """Status snapshot for /exotel/health (no secrets). status ∈
    {disabled, misconfigured, ok}; `ok` is true only when enabled AND valid."""
    errors, warnings = config.check()
    enabled = config.EXOTEL_ENABLED
    status = "disabled" if not enabled else ("misconfigured" if errors else "ok")
    return {
        "service": "exotel-agentstream",
        "status": status,
        "ok": enabled and not errors,
        "errors": errors,
        "warnings": warnings,
        "config": config.summary(),
    }


def register(app) -> None:
    """Mount the Exotel endpoints on the FastAPI app.

    /exotel/health is ALWAYS mounted (read-only, harmless). The /exotel/ws endpoint
    is mounted only when EXOTEL_ENABLED is true and startup validation passes."""

    @app.get(config.EXOTEL_HEALTH_PATH)
    async def exotel_health() -> dict:  # noqa: WPS430 (endpoint closure)
        return health_payload()

    if not config.EXOTEL_ENABLED:
        logger.info("Exotel integration disabled (set EXOTEL_ENABLED=true to enable); health at %s",
                    config.EXOTEL_HEALTH_PATH)
        return

    # Startup validation — a hard misconfig means we do NOT mount the WS endpoint
    # (the browser service stays up; /exotel/health reports 'misconfigured').
    try:
        config.validate()
    except ExotelConfigError as exc:
        logger.error("Exotel NOT mounted — invalid config: %s", exc)
        return

    sample_rate = config.sample_rate()

    @app.websocket(config.EXOTEL_WS_PATH)
    async def exotel_ws(websocket: WebSocket) -> None:  # noqa: WPS430 (endpoint closure)
        call_id = uuid.uuid4().hex[:8]
        set_call_id(call_id)  # every log line for this call (and its child tasks) is tagged
        await websocket.accept()
        # The IVR selects the language by DTMF BEFORE the stream connects and encodes
        # it in the query string: /exotel/ws?locale=en-IN (press 1) or ?locale=hi-IN
        # (press 2). Locale is FIXED for the call — there is no mid-call switch (stray
        # dtmf frames are already ignored in the read loop). English needs the echo
        # gate (NO_INTERRUPTION, no browser mic-gate on a phone); Hindi keeps barge-in.
        locale = (websocket.query_params.get("locale") or "").strip()
        logger.info("Exotel AgentStream connection accepted on %s (locale=%r)", config.EXOTEL_WS_PATH, locale)
        adapter = ExotelWebSocketAdapter(
            websocket, exotel_rate=sample_rate,
            gate_caller_audio=(locale == "en-IN"), locale=locale or "hi-IN")
        adapter.start()  # begin reading the Exotel stream
        session = make_exotel_session(locale, adapter)  # single locale->pipeline router
        try:
            # The SAME pipeline the browser runs. All STT/Gemini/TTS failure
            # handling lives inside run(); anything that still escapes is caught
            # here so the phone call is always torn down cleanly.
            await session.run()
        except WebSocketDisconnect:
            logger.info("Exotel caller disconnected")
        except Exception:
            logger.exception("Exotel session ended with an error")
        finally:
            await adapter.close()
            logger.info("Exotel call torn down")

    logger.info("Exotel AgentStream endpoint mounted at %s (sample_rate=%d); health at %s",
                config.EXOTEL_WS_PATH, sample_rate, config.EXOTEL_HEALTH_PATH)
