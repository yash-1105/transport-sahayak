"""Reusable multi-intent helpline tools — facility lookup, curated-KB info, and
complaint logging — for a general highway-helpline assistant.

Language-agnostic and self-contained: `HELPLINE_TOOL_DECLARATIONS` are the Gemini
function schemas, and `HelplineToolsMixin` provides the handlers. A dispatcher
session mixes this in (`class X(HelplineToolsMixin, DispatcherSession)`) and gets
three extra tools on top of the accident tools. Wired into the HINDI dispatcher
today; English (`dispatcher_live.py`) does NOT import or inherit any of this, so
the English pipeline is unchanged and can adopt it later without a rewrite.

The handlers reuse the session's existing plumbing:
- `self._safe_send_json` + a request/response future (same shape as
  `get_current_location`) for the two tools that need the browser's responder
  data (facility lookup, complaint logging);
- `self.state.location` for the caller's position;
- the curated `knowledge_base` for scheme/legal/what-to-do facts (never invented).

Honesty (Hard Rules): facility answers come from the app's real responder data
and are labelled ESTIMATES (no open/closed claim — not stored); scheme/legal
facts come ONLY from the KB; complaints get a REAL reference number and honest
"logged in the system" framing (no fabricated escalation).
"""

import asyncio
import logging
import uuid
from typing import Optional

from .knowledge_base import INFO_TOPICS, lookup_info

logger = logging.getLogger("helpline_tools")

_FACILITY_TIMEOUT_S = 8.0
_COMPLAINT_TIMEOUT_S = 8.0

FACILITY_TYPES = ["hospital", "ambulance", "mechanic", "tow", "fuel", "police", "fire"]
COMPLAINT_TYPES = ["pothole", "road_defect", "hazard", "other"]

# The three multi-intent tool schemas (appended to the shared accident-tool
# declarations only for the sessions that opt in — never mutated into the shared
# _TOOL_DECLARATIONS).
HELPLINE_TOOL_DECLARATIONS = [
    {
        "name": "find_nearest_facility",
        "description": (
            "Find the single NEAREST facility to the caller's current location and its estimated "
            "drive-time, from the helpline's real responder data. Use this for 'nearest hospital / "
            "mechanic / tow / fuel / police / ambulance / fire station' questions, for 'how long will "
            "the ambulance/mechanic take' (ETA) questions, and for vehicle breakdowns (mechanic/tow). "
            "This does NOT report whether a place is open or closed (that is not tracked) -- give the "
            "nearest facility and contact honestly. Do NOT use this to report an accident."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "facility_type": {
                    "type": "STRING",
                    "enum": FACILITY_TYPES,
                    "description": "Which kind of facility the caller needs.",
                },
                "capability": {
                    "type": "STRING",
                    "description": (
                        "Optional capability to prefer, e.g. 'trauma' or 'head injury' for a hospital. "
                        "Omit if the caller didn't ask for a specific capability."
                    ),
                },
            },
            "required": ["facility_type"],
        },
    },
    {
        "name": "answer_info_question",
        "description": (
            "Answer a scheme / legal / what-to-do question using ONLY the official curated knowledge "
            "base -- never your own knowledge. Use this for questions about: the golden-hour cashless "
            "treatment / PM-RAHAT scheme, the Rah-Veer / Good Samaritan protection and reward, "
            "hit-and-run compensation, Ayushman Bharat free treatment, motor-insurance-claim basics, "
            "whether to move the vehicle/victim after a crash, and what to do at night. NEVER state a "
            "scheme amount, eligibility, or legal fact from your own memory -- always call this and "
            "speak only what it returns (including its caution note). If the caller asks about "
            "something outside these topics, say honestly you don't have that specific information and "
            "suggest calling 1033."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "topic": {
                    "type": "STRING",
                    "enum": INFO_TOPICS,
                    "description": "The closest-matching knowledge-base topic key for the caller's question.",
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "lodge_complaint",
        "description": (
            "Log a road-defect or citizen complaint (pothole, broken barrier / crash-guard, a "
            "non-injury road hazard, or a general complaint) into the system and return a real "
            "reference number. Use this when the caller wants to REPORT a problem or complaint that "
            "is NOT an injury accident. Be honest with the caller: it is logged in the system with a "
            "reference number -- do NOT claim it was escalated to any official or that it will be "
            "fixed by a certain time. The caller's current location is attached automatically."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description": {
                    "type": "STRING",
                    "description": "A short description of the complaint / defect, written in English.",
                },
                "complaint_type": {
                    "type": "STRING",
                    "enum": COMPLAINT_TYPES,
                    "description": "The category of the complaint.",
                },
            },
            "required": ["description"],
        },
    },
]

# Tool names this mixin owns — used by the session's _dispatch_tool override.
HELPLINE_TOOL_NAMES = frozenset(d["name"] for d in HELPLINE_TOOL_DECLARATIONS)


class HelplineToolsMixin:
    """Handlers for the multi-intent helpline tools. Assumes it is mixed into a
    DispatcherSession subclass (provides `_safe_send_json`, `state`, etc.). The
    subclass must call `_init_helpline_state()` in __init__."""

    def _init_helpline_state(self) -> None:
        # Pending browser round-trips, keyed by requestId (same pattern as
        # DispatcherSession._pending_location).
        self._pending_facility: dict = {}
        self._pending_complaint: dict = {}

    # ── find_nearest_facility ────────────────────────────────────────────────
    async def _tool_find_nearest_facility(self, facility_type: str = "", capability: str = "") -> dict:
        ft = (facility_type or "").strip().lower()
        if ft not in FACILITY_TYPES:
            return {"ok": False, "error": f"Unknown facility_type -- choose one of {FACILITY_TYPES}."}
        if not self.state.location:
            return {
                "ok": False,
                "needs_location": True,
                "message": (
                    "The caller's location is not known yet. Ask them to share it using the map-pin "
                    "button (or describe a nearby landmark), then try again."
                ),
            }
        request_id = str(uuid.uuid4())
        fut: "asyncio.Future" = asyncio.get_event_loop().create_future()
        self._pending_facility[request_id] = fut
        await self._safe_send_json({
            "type": "request_facility",
            "requestId": request_id,
            "facilityType": ft,
            "capability": (capability or "").strip() or None,
            "location": self.state.location,
        })
        try:
            return await asyncio.wait_for(fut, timeout=_FACILITY_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._pending_facility.pop(request_id, None)
            return {"ok": False, "error": "Facility lookup timed out -- tell the caller to try again in a moment."}

    # ── answer_info_question (pure backend, curated KB) ──────────────────────
    async def _tool_answer_info_question(self, topic: str = "") -> dict:
        entry = lookup_info(topic)
        if entry is None:
            return {
                "ok": False,
                "found": False,
                "known_topics": INFO_TOPICS,
                "message": (
                    "This exact question is not in the knowledge base. Tell the caller honestly that "
                    "you don't have that specific information and suggest calling 1033 -- do NOT invent "
                    "an amount, eligibility, or legal answer."
                ),
            }
        return {
            "ok": True,
            "found": True,
            "topic": topic,
            "answer_hi": entry["hi"],
            "answer_en": entry["en"],
            "note_hi": entry.get("note_hi", ""),
            "note_en": entry.get("note_en", ""),
            "source": entry["source"],
            "instruction": (
                "Speak ONLY the facts in answer_hi (rephrased naturally, in your own warm spoken Hindi) "
                "plus the caution in note_hi. Do not add any amount, date, eligibility or legal claim "
                "that is not here."
            ),
        }

    # ── lodge_complaint ──────────────────────────────────────────────────────
    async def _tool_lodge_complaint(self, description: str = "", complaint_type: str = "road_defect") -> dict:
        desc = (description or "").strip()
        if not desc:
            return {"ok": False, "error": "Ask the caller for a short description of the problem first."}
        ct = complaint_type if complaint_type in COMPLAINT_TYPES else "other"
        request_id = str(uuid.uuid4())
        fut: "asyncio.Future" = asyncio.get_event_loop().create_future()
        self._pending_complaint[request_id] = fut
        await self._safe_send_json({
            "type": "request_complaint",
            "requestId": request_id,
            "description": desc[:500],
            "complaintType": ct,
            "location": self.state.location,
        })
        try:
            return await asyncio.wait_for(fut, timeout=_COMPLAINT_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._pending_complaint.pop(request_id, None)
            return {"ok": False, "error": "Complaint logging timed out -- tell the caller to try again in a moment."}

    # ── dispatch + browser-reply plumbing (used by the session) ──────────────
    async def _maybe_dispatch_helpline_tool(self, name: str, args: dict) -> Optional[dict]:
        """Run a helpline tool if `name` is one of ours; else return None so the
        caller falls back to the shared accident-tool dispatch."""
        handlers = {
            "find_nearest_facility": self._tool_find_nearest_facility,
            "answer_info_question": self._tool_answer_info_question,
            "lodge_complaint": self._tool_lodge_complaint,
        }
        handler = handlers.get(name)
        if handler is None:
            return None
        try:
            return await handler(**args)
        except Exception:
            logger.exception("Helpline tool %s failed", name)
            return {"ok": False, "error": "Internal error running this tool -- please try again."}

    def _resolve_helpline_client_message(self, mtype: str, msg: dict) -> bool:
        """Resolve a facility/complaint reply future from the browser pump.
        Returns True iff it handled the message (so the pump can skip it)."""
        if mtype == "facility_result":
            fut = self._pending_facility.pop(msg.get("requestId"), None)
            if fut and not fut.done():
                fut.set_result({
                    "ok": True,
                    "facility": msg.get("facility"),        # None if none found
                    "needs_location": bool(msg.get("needsLocation")),
                })
            return True
        if mtype == "complaint_result":
            fut = self._pending_complaint.pop(msg.get("requestId"), None)
            if fut and not fut.done():
                ref = msg.get("referenceId")
                fut.set_result({"ok": bool(ref), "reference_id": ref})
            return True
        return False
