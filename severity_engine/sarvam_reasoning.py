"""sarvam_reasoning.py — Sarvam `sarvam-105b-conversations` reasoning backend for the
HINDI dispatcher only. Sarvam is the PRIMARY reasoner; Gemini stays as the
per-turn fallback (see dispatcher_hindi._reason). OpenAI-compatible
chat-completions on api.sarvam.ai, reusing SARVAM_API_KEY — no new auth, in-region
(India), no Vertex quota. English (dispatcher_live.py) is untouched.

This module is pure transport + format mapping, unit-testable offline with a mocked
client:
  - gemini_tools_to_openai()  : the shared Gemini function-declaration DICTS ->
                                OpenAI `tools` (only difference is the Schema TYPE
                                enum casing: OBJECT/STRING -> object/string).
  - gemini_history_to_openai_messages() : the dispatcher's Gemini `types.Content`
                                history -> OpenAI `messages`, so ONE canonical
                                history drives both backends.
  - sarvam_generate()         : one streaming chat completion, aggregated + parsed
                                into a NormalizedResult whose tool_calls are the
                                SAME shape the Gemini path already produces (name +
                                dict args + id), so the existing tool handlers,
                                fast-path guards, and _history mirroring are
                                untouched.
Verified live (2026-08): the key can call sarvam-105b-conversations; tool_calls come
back in OpenAI format; usage.completion_tokens_details is null and reasoning_content
is null => reasoning_effort:low adds no hidden reasoning tokens.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("sarvam_reasoning")

_CHAT_URL = os.environ.get("SARVAM_CHAT_URL", "https://api.sarvam.ai/v1/chat/completions")
# HINDI_REASONING_MODEL selects the model; default is the reliability-proven
# sarvam-105b-conversations. Phase 2 benchmarks smaller/faster models (sarvam-30b,
# gemma) and only lowers this default if one passes the tool-calling reliability bar.
_MODEL = os.environ.get("HINDI_REASONING_MODEL") or os.environ.get("SARVAM_REASONING_MODEL", "sarvam-105b-conversations")
_REASONING_EFFORT = os.environ.get("SARVAM_REASONING_EFFORT", "low")
_TIMEOUT_S = float(os.environ.get("SARVAM_REASONING_TIMEOUT_S", "5"))  # tight: p50 ~1.8s, max ~4.9s; keeps the 2-attempt budget small
_TEMPERATURE = float(os.environ.get("SARVAM_REASONING_TEMPERATURE", "0.4"))


class SarvamReasoningError(RuntimeError):
    """Any Sarvam-turn failure (transport, HTTP, empty, malformed tool call) — the
    dispatcher catches this and falls back to Gemini for that turn."""


# ── Gemini function-declaration dicts -> OpenAI tools ─────────────────────────
_GEMINI_TYPE_MAP = {
    "OBJECT": "object", "STRING": "string", "NUMBER": "number",
    "INTEGER": "integer", "BOOLEAN": "boolean", "ARRAY": "array",
}


def _schema_to_jsonschema(schema):
    """Recursively lower-case the Gemini Schema TYPE enum (OBJECT->object, ...) to
    plain JSON Schema; everything else (properties/required/enum/items/description)
    is already JSON-Schema-shaped in the shared declarations."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            out[k] = _GEMINI_TYPE_MAP.get(v, v.lower())
        elif k == "properties" and isinstance(v, dict):
            out[k] = {pk: _schema_to_jsonschema(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = _schema_to_jsonschema(v)
        else:
            out[k] = v
    return out


def gemini_tools_to_openai(declarations: list) -> list:
    """Map the shared Gemini function-declaration DICTS to OpenAI `tools`."""
    tools = []
    for d in declarations:
        params = d.get("parameters") or {"type": "object", "properties": {}}
        tools.append({
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d.get("description", ""),
                "parameters": _schema_to_jsonschema(params),
            },
        })
    return tools


# ── Gemini types.Content history -> OpenAI messages ───────────────────────────
def _response_payload(fr) -> dict:
    resp = getattr(fr, "response", None)
    if isinstance(resp, dict):
        return resp
    return {"result": resp} if resp is not None else {}


def gemini_history_to_openai_messages(history: list, system_prompt: str) -> list:
    """Translate the dispatcher's canonical Gemini `types.Content` history into
    OpenAI `messages`, so the SAME history drives both backends. Gemini uses role
    "user" for both caller text and function RESPONSES, so a Content carrying
    function_response parts becomes OpenAI `tool` messages (ids preserved so they
    match the assistant tool_calls); role "model" -> "assistant" (+ tool_calls)."""
    msgs: list = [{"role": "system", "content": system_prompt}]
    for content in history:
        role = getattr(content, "role", None)
        parts = getattr(content, "parts", None) or []
        texts, tool_calls, tool_results = [], [], []
        for p in parts:
            if getattr(p, "text", None):
                texts.append(p.text)
            if getattr(p, "function_call", None):
                tool_calls.append(p.function_call)
            if getattr(p, "function_response", None):
                tool_results.append(p.function_response)
        if tool_results:  # Gemini role "user" carrying tool results -> OpenAI tool msgs
            for fr in tool_results:
                msgs.append({
                    "role": "tool",
                    "tool_call_id": getattr(fr, "id", None) or getattr(fr, "name", "") or "call_0",
                    "content": json.dumps(_response_payload(fr), ensure_ascii=False, default=str),
                })
        elif role == "model":
            m: dict = {"role": "assistant", "content": " ".join(texts) or None}
            if tool_calls:
                m["tool_calls"] = [{
                    "id": getattr(fc, "id", None) or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": fc.name,
                        "arguments": json.dumps(dict(getattr(fc, "args", None) or {}), ensure_ascii=False, default=str),
                    },
                } for i, fc in enumerate(tool_calls)]
            msgs.append(m)
        else:  # caller text
            msgs.append({"role": "user", "content": " ".join(texts)})
    return msgs


# ── Normalized result (SAME shape the Gemini parse produces) ──────────────────
class NormalizedToolCall:
    __slots__ = ("id", "name", "args")

    def __init__(self, id: Optional[str], name: str, args: dict):
        self.id = id
        self.name = name
        self.args = args


class NormalizedResult:
    def __init__(self, text: str, tool_calls: list, reasoning_tokens: int = 0):
        self.text = text
        self.tool_calls = tool_calls
        self.reasoning_tokens = reasoning_tokens

    @property
    def ok(self) -> bool:
        return bool(self.text or self.tool_calls)


def _api_key() -> str:
    k = os.environ.get("SARVAM_API_KEY")
    if not k:
        raise SarvamReasoningError("SARVAM_API_KEY is not set")
    return k


async def sarvam_generate(messages: list, tools: list, *, max_tokens: int = 300,
                          temperature: Optional[float] = None, timeout: Optional[float] = None,
                          client: Optional[httpx.AsyncClient] = None, model: Optional[str] = None) -> NormalizedResult:
    """One streaming Sarvam chat completion, aggregated + parsed into a
    NormalizedResult. Raises SarvamReasoningError on transport/HTTP/empty/malformed
    so the dispatcher falls back to Gemini for that turn. `client` is injectable for
    offline tests / a persistent keep-alive connection; `model` overrides _MODEL
    (the Phase-2 benchmark drives several models through this one path)."""
    body = {
        "model": model or _MODEL,
        "messages": messages,
        "reasoning_effort": _REASONING_EFFORT,
        "stream": True,
        "temperature": _TEMPERATURE if temperature is None else temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = tools
    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}

    content_parts: list = []
    tc_acc: dict = {}          # index -> {"id","name","args"}
    reasoning_tokens = 0
    own = client is None
    c = client or httpx.AsyncClient(timeout=timeout or _TIMEOUT_S)
    try:
        async with c.stream("POST", _CHAT_URL, headers=headers, json=body) as r:
            if r.status_code != 200:
                detail = (await r.aread())[:200]
                raise SarvamReasoningError(f"HTTP {r.status_code}: {detail!r}")
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    d = json.loads(payload)
                except Exception:
                    continue
                choice = (d.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content_parts.append(delta["content"])
                for tc in (delta.get("tool_calls") or []):
                    idx = tc.get("index", 0)
                    slot = tc_acc.setdefault(idx, {"id": None, "name": None, "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
                usage = d.get("usage")
                if usage:
                    details = usage.get("completion_tokens_details") or {}
                    reasoning_tokens = details.get("reasoning_tokens") or reasoning_tokens
    except SarvamReasoningError:
        raise
    except Exception as e:
        raise SarvamReasoningError(f"stream failed: {type(e).__name__}: {e}") from e
    finally:
        if own:
            await c.aclose()

    tool_calls = []
    for idx in sorted(tc_acc):
        slot = tc_acc[idx]
        if not slot["name"]:
            continue
        raw = slot["args"].strip()
        if not raw:
            args = {}
        else:
            try:
                # raw_decode parses the FIRST valid JSON object and ignores trailing
                # data: sarvam-105b-conversations streams a NO-ARG tool's "{}" TWICE
                # ("{}{}"), which json.loads rejects as "Extra data" but is just a
                # duplicated empty object. Genuinely truncated JSON still raises.
                args, _ = json.JSONDecoder().raw_decode(raw)
            except Exception as e:
                raise SarvamReasoningError(f"malformed tool arguments for {slot['name']}: {e}")
        if not isinstance(args, dict):
            raise SarvamReasoningError(f"tool arguments for {slot['name']} were not an object")
        tool_calls.append(NormalizedToolCall(slot["id"] or f"call_{idx}", slot["name"], args))

    result = NormalizedResult("".join(content_parts).strip(), tool_calls, reasoning_tokens)
    if not result.ok:
        raise SarvamReasoningError("empty response (no content, no tool calls)")
    return result
