"""Structured, per-call logging for the Exotel integration.

Every Exotel log line is tagged with the id of the call it belongs to, e.g.

    2026-08-06 12:00:00 INFO [exotel.session] [call=3f9a1c2e] Exotel call started ...

The call id is carried in a `contextvars.ContextVar`, so it does NOT have to be
threaded through every function. Each WebSocket connection is served in its own
asyncio Task; `set_call_id()` is called once when the connection is accepted, and
because `asyncio.create_task` copies the current context, every child task the call
spawns (the read loop, the per-round-trip service tasks) inherits the same id
automatically. Nothing bleeds between concurrent calls.

`get_logger()` returns a LoggerAdapter that prepends `[call=<id>]` to the message
itself, so the tag shows up regardless of the app's global logging format (which
this integration deliberately does not modify).
"""
from __future__ import annotations

import contextvars
import logging

_call_id: "contextvars.ContextVar[str]" = contextvars.ContextVar("exotel_call_id", default="-")


def set_call_id(call_id: str) -> None:
    """Bind the current call's id to this task's context (and its child tasks)."""
    _call_id.set(call_id or "-")


def get_call_id() -> str:
    return _call_id.get()


class _CallAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        cid = _call_id.get()
        return (f"[call={cid}] {msg}" if cid and cid != "-" else msg), kwargs


def get_logger(name: str) -> logging.LoggerAdapter:
    """A logger that automatically tags messages with the current call id."""
    return _CallAdapter(logging.getLogger(name), {})
