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
import time
from typing import Optional

from severity_engine.dispatcher_hindi import (
    HindiDispatcherSession,
    _HINDI_OPENING_LINE,
    _OPENING_FALLBACK_QUESTION,
)

from . import config, protocol, services
from .audio_adapter import AudioAdapter
from .location import GeocodeLocationProvider, LocationOutcome
from .logging_utils import get_logger, mask_phone

logger = get_logger("exotel.session")

# Short Hindi "hold on, I'm looking" lines spoken WHILE a slow lookup (responder
# search + drive-time ETA, both HTTP round-trips) runs, so a phone caller never
# hears dead air and thinks the call dropped. One per facility kind; the model
# then speaks the actual result once it returns.
_FACILITY_FILLERS = {
    "hospital": "एक पल... मैं आपके सबसे नज़दीकी अस्पताल की जानकारी निकाल रहा हूँ।",
    "mechanic": "एक पल... मैं आपके पास का मैकेनिक ढूँढ रहा हूँ।",
    "fuel": "एक पल... मैं आपके पास का पेट्रोल पंप देख रहा हूँ।",
    "police": "एक पल... मैं आपके पास का पुलिस स्टेशन देख रहा हूँ।",
    "tow": "एक पल... मैं आपके पास की टो और रिकवरी सेवा देख रहा हूँ।",
    "ambulance": "एक पल... मैं आपके पास की एम्बुलेंस सेवा देख रहा हूँ।",
}
_FACILITY_FILLER_DEFAULT = "एक पल... मैं आपके लिए यह जानकारी निकाल रहा हूँ।"
_COMPLAINT_FILLER = "एक पल... मैं आपकी शिकायत दर्ज कर रहा हूँ।"

# Exotel requires outbound media payloads to be MULTIPLES OF 320 BYTES, <= 100 KB
# per frame (per the AgentStream spec). Bulbul chunks are arbitrary sizes, so we
# buffer resampled PCM and emit only 320-aligned frames. 320 bytes = 20 ms @ 8 kHz
# and is a common divisor of a 20 ms frame at 8/16/24 kHz, so this holds at any rate.
_OUT_FRAME_ALIGN = 320
_OUT_FRAME_MAX = 32000  # 100 * 320; ~200 ms @ 8 kHz / ~100 ms @ 16 kHz, well under 100 KB

# The shared Hindi system prompt tells the model to ask the caller to send their
# location via the browser's MAP-PIN BUTTON — there is no such button on a phone,
# so the model improvises ("pin your location via text message"). For the Exotel
# (phone) path only, rewrite those instructions: accept the caller's APPROXIMATE
# spoken location (area / landmark / town / highway) and move forward; never ask
# them to pin, text, or open an app; ask at most once for a nearby landmark.
_EXOTEL_LOCATION_ADDENDUM = (
    "\n\n—— फ़ोन कॉल — लोकेशन नियम (यह ऊपर की हर 'मैप-पिन'/'लोकेशन भेजने' वाली बात पर भारी पड़ता है) ——\n"
    "यह एक असली फ़ोन कॉल है — यहाँ कोई ऐप, कोई स्क्रीन, कोई बटन, कोई मैसेज या SMS नहीं है। "
    "caller से कभी भी लोकेशन 'पिन' करने, कोई मैसेज/टेक्स्ट भेजने, या ऐप/स्क्रीन खोलने को न कहें। "
    "caller जो भी जगह बोले — इलाका, मोहल्ला, लैंडमार्क, पेट्रोल पंप, टोल प्लाज़ा, हाईवे नंबर, या शहर/गाँव का नाम — "
    "उसी अनुमानित लोकेशन को स्वीकार करके आगे बढ़ें। सटीक पता या GPS कभी न माँगें। "
    "अगर caller ने अभी तक जगह नहीं बताई, तो सिर्फ़ एक बार सहजता से पास का कोई लैंडमार्क पूछ लें "
    "(जैसे \"आप किस इलाके या किस लैंडमार्क के पास हैं?\"), और जो बताएं उसी अनुमानित जगह से काम चलाएं।"
)


def _phone_location_prompt(base: str) -> str:
    """Rewrite the shared Hindi prompt's browser map-pin location instructions for
    a phone call (approximate spoken location, never pin/text). Returns the base
    unchanged (plus the override addendum) if the wording drifts — the addendum
    still steers the model, and tests assert no 'map-pin' instruction survives."""
    if not isinstance(base, str):
        return base
    p = base.replace(
        "अगर टूल कहे कि लोकेशन नहीं पता, तो पहले caller से मैप-पिन बटन से अपनी लोकेशन भेजने को कहें, फिर दोबारा बुलाएं।",
        "अगर टूल कहे कि लोकेशन नहीं पता, तो caller से पास का कोई इलाका या लैंडमार्क पूछकर वही अनुमानित जगह इस्तेमाल करें, फिर टूल दोबारा बुलाएं।",
    ).replace(
        "वरना caller से मैप-पिन बटन से लोकेशन भेजने को कहें।",
        "वरना caller से पास का कोई इलाका या लैंडमार्क पूछकर उसी अनुमानित जगह से आगे बढ़ें।",
    )
    # Safety net for any remaining mention if the base wording drifts.
    p = p.replace("मैप-पिन बटन", "पास का लैंडमार्क")
    return p + _EXOTEL_LOCATION_ADDENDUM


class ExotelWebSocketAdapter:
    """Quacks like a FastAPI WebSocket for the session; wraps the real Exotel WS."""

    def __init__(self, exotel_ws, exotel_rate: int = 8000):
        self._exotel = exotel_ws
        self._audio = AudioAdapter(exotel_rate)
        self._inbound: "asyncio.Queue[dict]" = asyncio.Queue()
        self._reader: Optional[asyncio.Task] = None
        self._responders: Optional[dict] = None
        self._closed = False
        self._out_buf = bytearray()  # outbound PCM awaiting 320-byte-aligned framing
        # latency milestones (monotonic seconds), stamped once each; see _stamp.
        self._t: dict = {}
        self._t0 = time.monotonic()
        self._summary_logged = False
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

    def _stamp(self, name: str) -> None:
        """Record a latency milestone the first time it happens."""
        if name not in self._t:
            self._t[name] = time.monotonic()
            config.dbg(logger, "milestone %s @ +%.0fms", name, (self._t[name] - self._t0) * 1000)

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
                if ev.kind != "media":
                    config.dbg(logger, "← %s", ev.kind)
                if ev.kind == "start":
                    self.stream_sid = ev.stream_sid or self.stream_sid
                    self.call_sid = ev.call_sid
                    self.from_number = ev.from_number
                    if ev.sample_rate and ev.sample_rate != self._audio.exotel_rate:
                        self._audio = AudioAdapter(ev.sample_rate)  # trust the applet's actual rate
                    self._stamp("connected")
                    logger.info("Exotel call started (call_sid=%s from=%s rate=%dHz)",
                                self.call_sid, mask_phone(self.from_number), self._audio.exotel_rate)
                elif ev.kind == "media" and ev.audio:
                    self._stamp("first_audio_in")
                    pcm16k = self._audio.exotel_to_pipeline(ev.audio)
                    self._inbound.put_nowait({"type": "websocket.receive", "bytes": pcm16k})
                    config.dbg(logger, "← media %dB @%dHz -> %dB @16k",
                               len(ev.audio), self._audio.exotel_rate, len(pcm16k))
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
        """Bulbul PCM16 @ 24 kHz -> Exotel media, resampled and framed to 320-byte
        multiples (Exotel requires that; Bulbul chunks are arbitrary sizes)."""
        if self._closed or not data:
            return
        self._stamp("first_reply_audio")
        self._out_buf += self._audio.pipeline_to_exotel(data)
        await self._flush_out(final=False)

    async def _flush_out(self, final: bool) -> None:
        """Emit buffered outbound PCM as 320-aligned media frames (<= _OUT_FRAME_MAX
        each). Holds any sub-320-byte remainder for the next chunk; on `final` (call
        teardown) pads the remainder to a 320 boundary with silence and flushes it."""
        buf = self._out_buf
        n = (len(buf) // _OUT_FRAME_ALIGN) * _OUT_FRAME_ALIGN
        i = 0
        while i < n:
            end = min(i + _OUT_FRAME_MAX, n)  # min of two 320-multiples stays 320-aligned
            frame = bytes(buf[i:end])
            i = end
            await self._send_exotel(protocol.media_frame(self.stream_sid, frame))
            config.dbg(logger, "→ media %dB @%dHz", len(frame), self._audio.exotel_rate)
        del buf[:n]
        if final and buf:
            pad = (-len(buf)) % _OUT_FRAME_ALIGN
            frame = bytes(buf) + b"\x00" * pad
            buf.clear()
            await self._send_exotel(protocol.media_frame(self.stream_sid, frame))
            config.dbg(logger, "→ media %dB (final, padded)", len(frame))

    async def send_json(self, payload: dict) -> None:
        """Handle the session's outgoing control events. Browser-only events are
        ignored; the round-trips are answered server-side and injected back."""
        t = payload.get("type")
        if t == "transcript" and payload.get("role") in ("user", "caller"):
            self._stamp("first_stt")
            self.last_caller_utterance = payload.get("text") or self.last_caller_utterance
            config.dbg(logger, "STT transcript (%d chars)", len(payload.get("text") or ""))
        elif t == "request_facility":
            config.dbg(logger, "service → request_facility type=%s", payload.get("facilityType"))
            asyncio.create_task(self._service_facility(payload))
        elif t == "request_complaint":
            config.dbg(logger, "service → request_complaint type=%s", payload.get("complaintType"))
            asyncio.create_task(self._service_complaint(payload))
        elif t == "submitted":
            config.dbg(logger, "service → dispatch_update")
            asyncio.create_task(self._service_dispatch(payload))
        elif t == "interrupted":
            self._out_buf.clear()  # drop our buffered outbound audio on barge-in...
            await self._send_exotel(protocol.clear_frame(self.stream_sid))  # ...and Exotel's
            config.dbg(logger, "→ clear (barge-in)")
        elif t == "call_complete":
            self.call_complete = True  # the endpoint hangs up after run() returns
        # ready / status / form_update / turn_complete / tts_text / call_intent /
        # interim_dispatch / request_location(*): no Exotel action. (*request_location
        # never fires — ExotelHindiSession resolves location without a round-trip.)

    async def send_text(self, text: str) -> None:
        # The session never sends raw text frames; kept for surface-compatibility.
        return None

    async def close(self) -> None:
        if not self._closed:
            try:
                await self._flush_out(final=True)  # emit any buffered tail while the socket may still be open
            except Exception:
                logger.debug("final flush failed (socket likely closed)", exc_info=True)
        self._closed = True
        self._log_latency_summary()
        if self._reader:
            self._reader.cancel()
        try:
            await self._exotel.close()
        except Exception:
            pass

    # ── internals ────────────────────────────────────────────────────────────
    def _log_latency_summary(self) -> None:
        """One INFO line per call: transport milestones from connect to teardown.
        Gemini/Bulbul internal turn latencies come from the pipeline's own
        `[latency]` logs — this is the end-to-end transport view."""
        if self._summary_logged:
            return
        self._summary_logged = True

        def ms(name: str) -> str:
            return f"{(self._t[name] - self._t0) * 1000:.0f}ms" if name in self._t else "—"

        logger.info(
            "[latency-summary] first_audio_in=%s first_stt=%s first_reply_audio=%s total=%.1fs",
            ms("first_audio_in"), ms("first_stt"), ms("first_reply_audio"),
            time.monotonic() - self._t0)

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
        # Phone-call location prompt: rewrite the two gen configs the parent built
        # so the model accepts an APPROXIMATE spoken location and never asks the
        # caller to use a (non-existent) map-pin button. Scoped to THIS session's
        # configs only — the shared prompt and the browser path are untouched.
        phone_prompt = _phone_location_prompt(self._gen_config.system_instruction)
        self._gen_config = self._gen_config.model_copy(update={"system_instruction": phone_prompt})
        self._briefing_config = self._briefing_config.model_copy(update={"system_instruction": phone_prompt})

    async def _reason(self, gemini_client, user_text, config=None):
        """Skip the Gemini call on the OPENING turn only. On a phone the worst
        first impression is the multi-second wait (Vertex latency / 429 rate-limits)
        before the welcome line even starts. The opening is a fixed greeting + a
        generic open question anyway, so returning None here makes _agent_turn speak
        its deterministic fallback (greeting + _OPENING_FALLBACK_QUESTION) instantly,
        with no model round-trip. Every later turn reasons normally via super()."""
        if self._opening_line_pending:
            return None
        return await super()._reason(gemini_client, user_text, config=config)

    async def _speak_filler_during(self, filler: str, coro):
        """Speak a short holding line WHILE `coro` (a slow lookup) runs, then wait
        for both — so the filler finishes playing before the model's result reply is
        spoken (no overlap), and the caller hears speech instead of silence."""
        filler_task = asyncio.create_task(self._speak_or_fallback(filler, allow_bargein=False))
        try:
            return await coro
        finally:
            try:
                await filler_task
            except Exception:
                logger.debug("filler speak failed", exc_info=True)

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
        filler = _FACILITY_FILLERS.get((facility_type or "").lower(), _FACILITY_FILLER_DEFAULT)
        return await self._speak_filler_during(
            filler, super()._tool_find_nearest_facility(facility_type=facility_type, capability=capability))

    async def _tool_lodge_complaint(self, description: str = "", complaint_type: str = "road_defect") -> dict:
        if not self.state.location:
            outcome = await self._ensure_location()
            if outcome is not None:
                return {"ok": False, "needs_location": True,
                        "message": outcome.next_step or "Ask the caller where the problem is (a nearby landmark), then try again."}
        return await self._speak_filler_during(
            _COMPLAINT_FILLER, super()._tool_lodge_complaint(description=description, complaint_type=complaint_type))
