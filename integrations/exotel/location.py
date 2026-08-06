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

from typing import Awaitable, Callable, Optional

from . import services

MAX_ATTEMPTS = 3

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
