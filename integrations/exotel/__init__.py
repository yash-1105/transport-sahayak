"""Exotel AgentStream Voicebot integration — telephony transport ONLY.

This package adapts an Exotel phone call onto the EXISTING, unchanged Hindi voice
assistant (severity_engine.dispatcher_hindi.HindiDispatcherSession). It adds a
single WebSocket endpoint that wraps the Exotel stream in a browser-shaped
adapter and hands it to the same session the browser uses — so all reasoning,
prompts, Sarvam Saaras/Bulbul, multi-intent routing, dispatch, and SOP logic are
reused verbatim. Nothing in the browser pipeline, prompts, or business logic is
modified; this layer only translates the wire protocol + audio format and
services the browser round-trips (location/facility/complaint/dispatch)
server-side because a phone has no browser to answer them.

Hindi-only for now (English AgentStream support is a later, separate step).
"""
