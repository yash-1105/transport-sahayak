"""Transport-agnostic location acquisition for a phone call (composition).

The dispatcher only needs `state.location` populated — it doesn't care HOW. The
browser path fills it from device GPS (a `request_location` round-trip); the phone
path has no GPS, so it forward-geocodes the caller's spoken landmark. This provider
encapsulates that "phone landmark -> geocoder -> location object" path as a small,
injectable, testable unit that `ExotelHindiSession` COMPOSES (holds one), parallel
to the browser's provider being the GPS round-trip.

It is deliberately NOT wired into the base dispatcher: doing so would mean editing
dispatcher_live.py / dispatcher_hindi.py (the browser's source of truth, whose GPS
round-trip is woven through the core loop) — a change the constraints forbid. So
the provider is composed by the thin Exotel subclass instead, which is the minimal
seam that keeps the base 100% unchanged.

Policy: geocode the latest spoken landmark; if it fails, ask for a clearer one;
after `max_attempts` failures, terminate gracefully. NEVER substitute a default
location or inject fake coordinates.
"""
from __future__ import annotations

import re
from typing import Awaitable, Callable, Optional

from . import services

MAX_ATTEMPTS = 3

# Devanagari place cues. Their presence marks a caller utterance as a LOCATION
# statement, so the opportunistic backstop never geocodes a bare accident
# description ("दो कारें टकराईं") -- which Google Places would happily resolve to
# some unrelated business (verified live: it returns a random unrelated shop for that text).
_HI_LOCATION_CUES = (
    "नगर", "सेक्टर", "रोड", "मार्ग", "चौक", "बाज़ार", "बाजार", "मार्केट", "हाईवे",
    "पुल", "गली", "कॉलोनी", "कालोनी", "मोहल्ला", "गाँव", "गांव", "टोल", "पंप",
    "स्टैंड", "मंदिर", "गेट", "पास", "प्लाज़ा", "प्लाजा", "फ्लाईओवर", "बाईपास",
    "मोड़", "विहार", "पुरी", "गंज", "बस्ती", "एक्सप्रेसवे", "इलाके", "इलाका", "चौराहा",
)


def looks_like_location(text: str) -> bool:
    """Cheap pre-filter: is this utterance plausibly a PLACE statement (worth a
    geocode)? True if it has any Latin token >=3 chars (a romanized place/brand:
    'Ganeshguri', 'KFC', 'GS Road') OR a Devanagari location cue. A pure-
    Devanagari accident description ('दो कारें टकराईं') has neither, so it is
    skipped -- never geocoded into a bogus business match."""
    t = text or ""
    if re.search(r"[A-Za-z]{3,}", t):
        return True
    return any(cue in t for cue in _HI_LOCATION_CUES)


def label_verifies(query: str, label: str) -> bool:
    """Guard against Google Places returning SOME business for a non-place query.
    If the query has Latin tokens, require at least one to appear in the geocoded
    label ('ambulance please' -> 'Max Hospital' is rejected). A Devanagari-cue-only
    query (no Latin tokens) is trusted -- the cue already marked it a place and the
    English label can't be token-matched across scripts."""
    q = {t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", query or "")}
    if not q:
        return True  # Devanagari-cue path: trust (no cross-script token match possible)
    l = {t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", label or "")}
    return bool(q & l)

_ASK = (
    "You do not have the caller's location yet. Warmly ask them for ONE specific "
    "nearby landmark — a highway or NH number, a toll plaza, a petrol pump, or the "
    "town/village name — then continue. Never guess or assume a location.")
_TERMINATE = (
    "You could not determine the caller's location after several tries. Briefly "
    "apologise, tell them to call back with a clearer nearby landmark, and end the "
    "call now — do not keep asking.")


class LocationOutcome:
    """The result of one acquisition attempt.

    * ok            -> `location` is a {lat,lng,label} dict (success)
    * silent        -> nothing to geocode yet; do NOT prompt the caller
    * next_step     -> guidance for the model to ask for a clearer landmark
    * terminate     -> too many failures; guidance says to end the call
    """

    def __init__(self, location: Optional[dict] = None, silent: bool = False,
                 next_step: Optional[str] = None, terminate: bool = False):
        self.location = location
        self.silent = silent
        self.next_step = next_step
        self.terminate = terminate

    @property
    def ok(self) -> bool:
        return self.location is not None


class GeocodeLocationProvider:
    """Acquire location by forward-geocoding the caller's latest spoken landmark.

    `landmark_source()` returns the most recent caller utterance (the adapter keeps
    this updated). `geocode` maps text -> {lat,lng,label}|None; left None it
    late-binds to services.geocode_landmark at call time (so tests can monkeypatch
    the module function, and callers can inject a fake directly). The provider
    reports an outcome only — it never writes dispatcher state; the caller does.
    """

    def __init__(self, landmark_source: Callable[[], str],
                 geocode: Optional[Callable[[str], Awaitable[Optional[dict]]]] = None,
                 max_attempts: int = MAX_ATTEMPTS):
        self._landmark_source = landmark_source
        self._geocode = geocode  # None => late-bind to services.geocode_landmark
        self._max_attempts = max_attempts
        self._attempts = 0

    async def acquire(self) -> LocationOutcome:
        landmark = (self._landmark_source() or "").strip()
        if not landmark:
            return LocationOutcome(silent=True)
        geocode = self._geocode or services.geocode_landmark
        loc = await geocode(landmark)
        if loc:
            self._attempts = 0
            return LocationOutcome(location=loc)
        self._attempts += 1
        if self._attempts >= self._max_attempts:
            return LocationOutcome(next_step=_TERMINATE, terminate=True)
        return LocationOutcome(next_step=_ASK)

    async def try_opportunistic(self) -> Optional[dict]:
        """Backstop for when the MODEL forgets to call get_current_location after
        the caller states their location (observed live: it recorded other facts
        for 5 turns while never re-triggering the geocode, so location stayed
        unset and it re-asked every turn). Geocode the latest utterance IF it
        looks like a place, accept only a VERIFIED hit, and NEVER count a failure
        toward the ask/terminate budget (that budget is the model-driven loop's).
        Returns the location dict or None. Mirrors the incident-type/hazard-flag
        transcript backstops: don't depend on the model remembering to call a tool."""
        landmark = (self._landmark_source() or "").strip()
        if len(landmark) < 3 or not looks_like_location(landmark):
            return None
        geocode = self._geocode or services.geocode_landmark
        loc = await geocode(landmark)
        if loc and label_verifies(landmark, loc.get("label", "")):
            return loc
        return None
