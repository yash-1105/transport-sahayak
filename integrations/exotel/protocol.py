"""Exotel AgentStream WebSocket protocol — parse inbound events, build outbound.

Exotel's bidirectional voice-streaming (Voicebot AgentStream) is a JSON
WebSocket protocol very close to Twilio Media Streams. Messages carry an event
name and a `stream_sid` identifying the stream; audio is base64 in `media`
frames. This module ONLY (de)serialises those frames — no audio maths (see
audio_adapter.py), no session logic (see session.py).

Inbound (Exotel → us):
  connected  {"event":"connected"}
  start      {"event":"start","stream_sid":..,"start":{"call_sid":..,"from":..,"to":..,
                "media_format":{"encoding":"raw"/"pcm","sample_rate":16000,"channels":1},
                "custom_parameters":{..}}}
  media      {"event":"media","stream_sid":..,"media":{"chunk":N,"timestamp":..,"payload":<b64>}}
  dtmf       {"event":"dtmf","stream_sid":..,"dtmf":{"digit":"1"}}
  stop       {"event":"stop","stream_sid":..,"stop":{..}}
Outbound (us → Exotel):
  media      {"event":"media","stream_sid":..,"media":{"payload":<b64>}}
  clear      {"event":"clear","stream_sid":..}                 # flush buffered playback (barge-in)
  mark       {"event":"mark","stream_sid":..,"mark":{"name":..}}

The parser is deliberately tolerant (accepts `event` or `type`; finds
`stream_sid`/`streamSid` at the top level or nested) because Exotel's exact key
casing can vary by applet/version — confirm against the Voicebot applet docs and
adjust the small maps below if a field name differs. Unknown events are returned
as ExotelEvent(kind="unknown") rather than raising.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExotelEvent:
    kind: str                      # connected | start | media | dtmf | stop | unknown
    stream_sid: Optional[str] = None
    audio: Optional[bytes] = None  # decoded PCM bytes for a media event
    digit: Optional[str] = None    # for a dtmf event
    call_sid: Optional[str] = None
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    sample_rate: Optional[int] = None   # from start.media_format, if present
    custom_parameters: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def _stream_sid(msg: dict) -> Optional[str]:
    return msg.get("stream_sid") or msg.get("streamSid") or msg.get("streamId")


def parse_event(msg: dict) -> ExotelEvent:
    """Parse one decoded JSON message from Exotel into an ExotelEvent."""
    kind = (msg.get("event") or msg.get("type") or "unknown").lower()
    sid = _stream_sid(msg)

    if kind == "media":
        media = msg.get("media") or {}
        payload = media.get("payload") or ""
        try:
            audio = base64.b64decode(payload) if payload else b""
        except Exception:
            audio = b""
        return ExotelEvent(kind="media", stream_sid=sid, audio=audio, raw=msg)

    if kind == "start":
        start = msg.get("start") or {}
        fmt = start.get("media_format") or start.get("mediaFormat") or {}
        rate = fmt.get("sample_rate") or fmt.get("sampleRate")
        return ExotelEvent(
            kind="start",
            stream_sid=sid or start.get("stream_sid"),
            call_sid=start.get("call_sid") or start.get("callSid"),
            from_number=start.get("from"),
            to_number=start.get("to"),
            sample_rate=int(rate) if rate else None,
            custom_parameters=start.get("custom_parameters") or start.get("customParameters") or {},
            raw=msg,
        )

    if kind == "dtmf":
        dtmf = msg.get("dtmf") or {}
        return ExotelEvent(kind="dtmf", stream_sid=sid, digit=dtmf.get("digit"), raw=msg)

    if kind in ("connected", "stop"):
        return ExotelEvent(kind=kind, stream_sid=sid, raw=msg)

    return ExotelEvent(kind="unknown", stream_sid=sid, raw=msg)


# ── outbound frame builders ───────────────────────────────────────────────────
def media_frame(stream_sid: Optional[str], pcm: bytes) -> dict:
    """A media frame carrying raw PCM (already at Exotel's sample rate)."""
    frame = {"event": "media", "media": {"payload": base64.b64encode(pcm).decode("ascii")}}
    if stream_sid:
        frame["stream_sid"] = stream_sid
    return frame


def clear_frame(stream_sid: Optional[str]) -> dict:
    """Tell Exotel to drop any buffered outbound audio — used for barge-in."""
    frame = {"event": "clear"}
    if stream_sid:
        frame["stream_sid"] = stream_sid
    return frame


def mark_frame(stream_sid: Optional[str], name: str) -> dict:
    """A playback marker; Exotel echoes it back when that audio has played."""
    frame = {"event": "mark", "mark": {"name": name}}
    if stream_sid:
        frame["stream_sid"] = stream_sid
    return frame
