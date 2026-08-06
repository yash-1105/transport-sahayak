"""The bridge that lets the UNCHANGED Hindi pipeline run over an Exotel call.

`ExotelWebSocketAdapter` is a FastAPI-WebSocket look-alike (receive / send_json /
send_bytes / close) that `HindiDispatcherSession` talks to exactly as it talks to
the browser socket — so run(), Sarvam, Gemini, Bulbul, dispatch, and SOP logic
are reused verbatim. The adapter translates Exotel AgentStream ⇄ the browser
protocol and services the browser round-trips (facility / complaint / dispatch)
server-side (services.py).

`ExotelHindiSession` is a thin subclass of `HindiDispatcherSession` that overrides
ONLY the location-gated tools: a phone has no GPS, so location is acquired by
forward-geocoding the caller's spoken landmark, asking for a clearer one on
failure, and terminating after a few tries (never a silent default). Everything
else is inherited — no fork, no change to the base session or the browser.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from severity_engine.dispatcher_hindi import HindiDispatcherSession

from . import protocol, services
from .audio_adapter import AudioAdapter
from .location import GeocodeLocationProvider, LocationOutcome
from .logging_utils import get_logger

logger = get_logger("exotel.session")


class ExotelWebSocketAdapter:
    """Quacks like a FastAPI WebSocket for the session; wraps the real Exotel WS."""

    def __init__(self, exotel_ws, exotel_rate: int = 16000):
        self._exotel = exotel_ws
        self._audio = AudioAdapter(exotel_rate)
        self._inbound: "asyncio.Queue[dict]" = asyncio.Queue()
        self._reader: Optional[asyncio.Task] = None
        self._responders: Optional[dict] = None
        self._closed = False
        # exposed to ExotelHindiSession's location override + the endpoint
        self.stream_sid: Optional[str] = None
        self.call_sid: Optional[str] = None
        self.from_number: Optional[str] = None
        self.last_caller_utterance: str = ""
        self.call_complete = False
        self.query_params = {"locale": "hi-IN"}  # session doesn't read this; present for safety

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        """Read the real Exotel WS, translate events, feed the session's inbound
        queue. Media → 16 kHz PCM binary frames; stop/disconnect → end the call."""
        try:
            while True:
                message = await self._exotel.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                text = message.get("text")
                if text is None:
                    continue  # Exotel AgentStream is JSON text; ignore stray binary
                try:
                    ev = protocol.parse_event(json.loads(text))
                except Exception:
                    continue  # invalid packet -> drop, keep the call alive
                if ev.kind == "start":
                    self.stream_sid = ev.stream_sid or self.stream_sid
                    self.call_sid = ev.call_sid
                    self.from_number = ev.from_number
                    if ev.sample_rate and ev.sample_rate != self._audio.exotel_rate:
                        self._audio = AudioAdapter(ev.sample_rate)  # trust the applet's actual rate
                    logger.info("Exotel call started (call_sid=%s from=%s rate=%s)",
                                self.call_sid, self.from_number, self._audio.exotel_rate)
                elif ev.kind == "media" and ev.audio:
                    pcm16k = self._audio.exotel_to_pipeline(ev.audio)
                    self._inbound.put_nowait({"type": "websocket.receive", "bytes": pcm16k})
                elif ev.kind == "stop":
                    break
                # connected / dtmf / unknown: nothing to feed the pipeline (v1)
        except Exception:
            logger.debug("Exotel read loop ended", exc_info=True)
        finally:
            self._inbound.put_nowait({"type": "websocket.disconnect"})

    # ── FastAPI-WebSocket surface used by HindiDispatcherSession ─────────────
    async def receive(self) -> dict:
        return await self._inbound.get()

    async def send_bytes(self, data: bytes) -> None:
        """Bulbul PCM16 @ 24 kHz -> Exotel media (resampled)."""
        if self._closed or not data:
            return
        pcm = self._audio.pipeline_to_exotel(data)
        await self._send_exotel(protocol.media_frame(self.stream_sid, pcm))

    async def send_json(self, payload: dict) -> None:
        """Handle the session's outgoing control events. Browser-only events are
        ignored; the round-trips are answered server-side and injected back."""
        t = payload.get("type")
        if t == "transcript" and payload.get("role") in ("user", "caller"):
            self.last_caller_utterance = payload.get("text") or self.last_caller_utterance
        elif t == "request_facility":
            asyncio.create_task(self._service_facility(payload))
        elif t == "request_complaint":
            asyncio.create_task(self._service_complaint(payload))
        elif t == "submitted":
            asyncio.create_task(self._service_dispatch(payload))
        elif t == "interrupted":
            await self._send_exotel(protocol.clear_frame(self.stream_sid))  # barge-in
        elif t == "call_complete":
            self.call_complete = True  # the endpoint hangs up after run() returns
        # ready / status / form_update / turn_complete / tts_text / call_intent /
        # interim_dispatch / request_location(*): no Exotel action. (*request_location
        # never fires — ExotelHindiSession resolves location without a round-trip.)

    async def send_text(self, text: str) -> None:
        # The session never sends raw text frames; kept for surface-compatibility.
        return None

    async def close(self) -> None:
        self._closed = True
        if self._reader:
            self._reader.cancel()
        try:
            await self._exotel.close()
        except Exception:
            pass

    # ── internals ────────────────────────────────────────────────────────────
    async def _send_exotel(self, frame: dict) -> None:
        if self._closed:
            return
        try:
            await self._exotel.send_json(frame)
        except Exception:
            logger.debug("Exotel send failed (socket likely closed)", exc_info=True)

    def _inject(self, msg: dict) -> None:
        self._inbound.put_nowait({"type": "websocket.receive", "text": json.dumps(msg)})

    async def _get_responders(self) -> Optional[dict]:
        if self._responders is None:
            self._responders = await services.fetch_responders()
        return self._responders

    async def _service_facility(self, payload: dict) -> None:
        facility = None
        loc = payload.get("location")
        responders = await self._get_responders()
        if responders and loc:
            facility = await services.nearest_facility(
                responders, payload.get("facilityType", ""), (loc["lat"], loc["lng"]),
                payload.get("capability") or "")
        self._inject({"type": "facility_result", "requestId": payload.get("requestId"), "facility": facility})

    async def _service_complaint(self, payload: dict) -> None:
        loc = payload.get("location") or {}
        point = (loc["lat"], loc["lng"]) if loc.get("lat") is not None else None
        ref = await services.lodge_complaint(
            payload.get("description", ""), payload.get("complaintType", "other"),
            point, loc.get("label", ""))
        self._inject({"type": "complaint_result", "requestId": payload.get("requestId"), "referenceId": ref})

    async def _service_dispatch(self, payload: dict) -> None:
        incident = payload.get("incident") or {}
        loc = incident.get("location") or {}
        svc: dict = {}
        responders = await self._get_responders()
        if responders and loc.get("lat") is not None:
            svc = await services.build_dispatch_update(
                responders, (loc["lat"], loc["lng"]), set(incident.get("flags") or []))
        self._inject({"type": "dispatch_update", "services": svc})


class ExotelHindiSession(HindiDispatcherSession):
    """HindiDispatcherSession over an Exotel call. Overrides ONLY location
    acquisition (a phone has no GPS), by COMPOSING a GeocodeLocationProvider that
    turns the caller's spoken landmark into a location object. All reasoning /
    dispatch / SOP / tool logic is inherited unchanged.

    Why a subclass (inheritance) at all, when the transport itself is composed:
    the location tools live on the base session and gate on `self.state.location`,
    so the only place to swap GPS for geocoding without editing the browser's
    source-of-truth files is to override those three methods here. Each simply
    delegates to the composed provider — the geocode/retry/terminate policy is not
    duplicated in the session; it lives once in location.py."""

    def __init__(self, adapter: ExotelWebSocketAdapter):
        super().__init__(adapter)
        # Composition: the provider reads the latest caller utterance the adapter
        # tracks. The dispatcher doesn't know or care that location came from speech.
        self._location = GeocodeLocationProvider(
            landmark_source=lambda: getattr(self.websocket, "last_caller_utterance", "") or "")

    async def _ensure_location(self) -> Optional[LocationOutcome]:
        """None if location is already known or was just acquired (state.location
        set as a side effect); otherwise the outcome the caller should act on
        (silent / ask-for-landmark / terminate)."""
        if self.state.location:
            return None
        outcome = await self._location.acquire()
        if outcome.ok:
            self.state.location = outcome.location
            await self._safe_send_json({"type": "form_update", "field": "location", "value": outcome.location})
            return None
        return outcome

    async def _tool_get_current_location(self) -> dict:
        if self.state.location:
            return {"status": "already_have_location", **self.state.location, **self._state_block()}
        outcome = await self._ensure_location()
        if outcome is None:
            return {"status": "ok", **self.state.location, **self._state_block()}
        result = {"status": "unavailable", **self._state_block()}
        if not outcome.silent:
            result["next_step"] = outcome.next_step  # ask-for-landmark / terminate guidance
        return result

    async def _tool_find_nearest_facility(self, facility_type: str = "", capability: str = "") -> dict:
        if not self.state.location:
            outcome = await self._ensure_location()
            if outcome is not None:
                return {"ok": False, "needs_location": True,
                        "message": outcome.next_step or "Ask the caller for a specific nearby landmark, then try again."}
        return await super()._tool_find_nearest_facility(facility_type=facility_type, capability=capability)

    async def _tool_lodge_complaint(self, description: str = "", complaint_type: str = "road_defect") -> dict:
        if not self.state.location:
            outcome = await self._ensure_location()
            if outcome is not None:
                return {"ok": False, "needs_location": True,
                        "message": outcome.next_step or "Ask the caller where the problem is (a nearby landmark), then try again."}
        return await super()._tool_lodge_complaint(description=description, complaint_type=complaint_type)
