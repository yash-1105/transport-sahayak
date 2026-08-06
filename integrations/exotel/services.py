"""Server-side answers for the browser round-trips a phone call can't do itself.

REUSE, don't reimplement (constraint 3). The Python voice backend has NO responder
lookup / geocoding / ETA / complaint logic of its own — all of it lives in the
Next.js app (a separate deployment), reachable from here only over HTTP. So these
functions call the app's OWN real endpoints (the same ones the browser uses):

  * responder data   -> GET  {APP_BASE_URL}/api/aggregator/responders
  * drive-time ETA   -> POST {APP_BASE_URL}/api/routes/matrix  (traffic-aware Google
                        Routes — the SAME source the browser uses for hospital/police)
  * complaint record -> POST {APP_BASE_URL}/api/potholes
  * forward geocode  -> Nominatim (the same service the app's reverseGeocode uses)

The only local computation is nearest-by-distance selection + the app's own
haversine ETA formula for the synthetic ambulance/fire/tow posts (which have no
route to compute — the browser falls back to the identical formula there too). The
speed/buffer constants MIRROR src/lib/matching.ts by value; they cannot be imported
across the TS/Python boundary, so keep them in sync if matching.ts ever changes.

Config (env, all centralised in config.py): APP_BASE_URL, NOMINATIM_URL,
EXOTEL_HTTP_TIMEOUT, EXOTEL_HTTP_RETRIES. Every external call goes through `_send`,
which applies a per-attempt timeout + bounded retries with backoff.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
import uuid
from datetime import date
from typing import Any, Optional

import httpx

from . import config
from .logging_utils import get_logger

logger = get_logger("exotel.services")

# Delhi–Dehradun corridor bounding box. Nominatim viewbox is lon1,lat1,lon2,lat2.
# Bounds the forward-geocode so a bare landmark resolves inside the corridor,
# not to a same-named place elsewhere in India.
_CORRIDOR_VIEWBOX = "76.9,28.5,78.3,30.5"


async def _send(method: str, url: str, *, params: Optional[dict] = None,
                json: Optional[dict] = None, headers: Optional[dict] = None,
                label: str = "") -> Optional[Any]:
    """One external call with a per-attempt timeout and bounded retries + backoff.

    Retries only TRANSIENT failures (network errors, timeouts, 5xx); a 4xx is a
    caller error and is NOT retried. Returns the parsed JSON (dict or list), or
    None on give-up or a non-JSON body. Tuned by EXOTEL_HTTP_TIMEOUT /
    EXOTEL_HTTP_RETRIES (see config.py)."""
    attempts = config.HTTP_RETRIES + 1
    reason = "unknown"
    t0 = time.monotonic()
    for i in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as c:
                r = await c.request(method, url, params=params, json=json, headers=headers)
            if r.status_code >= 500:
                reason = f"HTTP {r.status_code}"  # transient -> retry
            elif r.status_code >= 400:
                logger.warning("%s: HTTP %s (client error, not retried)", label or url, r.status_code)
                return None
            else:
                config.dbg(logger, "%s: HTTP %s in %.0fms (attempt %d)",
                           label or url, r.status_code, (time.monotonic() - t0) * 1000, i + 1)
                try:
                    return r.json()
                except Exception:
                    return None  # 2xx but no/invalid JSON -> caller treats as no data
        except Exception as e:  # ConnectError / ReadTimeout / DNS / etc.
            reason = type(e).__name__
        if i < attempts - 1:
            await asyncio.sleep(config.HTTP_BACKOFF[min(i, len(config.HTTP_BACKOFF) - 1)])
    logger.warning("%s: gave up after %d attempt(s), %.0fms (%s)",
                   label or url, attempts, (time.monotonic() - t0) * 1000, reason)
    return None

# ── Canonical ETA business logic — mirrors src/lib/matching.ts EXACTLY ─────────
# haversineEtaMinutes(km, speed) = (km / speed) * 60 + EMERGENCY_ETA_BUFFER_MIN.
# Per-vehicle speeds match matching.ts (ambulance/fire 40, towing 50). The browser
# uses THIS same haversine for ambulance/fire/tow (no route for synthetic posts)
# and prefers Google Routes for hospital/police — so we do the same (route_eta
# below, haversine fallback). One formula, expressed once per language boundary.
_SPEED_KMPH = {"ambulance": 40.0, "fire": 40.0, "tow": 50.0, "hospital": 40.0, "police": 40.0}
_ETA_BUFFER_MIN = 3
_ROUTE_ETA_TYPES = {"hospital", "police"}  # the app uses live Google Routes for these

# facility_type -> which responder collection in /api/aggregator/responders.
# Registry types come from the top-level arrays; mechanic/fuel from Google POIs
# under `places` (car_repair / gas_station) — exactly the browser's mapping.
_REGISTRY_KEY = {
    "hospital": "hospitals", "ambulance": "ambulanceStations", "fire": "fireStations",
    "tow": "towingStations", "police": "policeStations",
}
_PLACES_KEY = {"mechanic": "car_repair", "fuel": "gas_station"}


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    R = 6371.0
    dlat = math.radians(b[0] - a[0]); dlng = math.radians(b[1] - a[1])
    la1 = math.radians(a[0]); la2 = math.radians(b[0])
    h = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


def _haversine_eta_minutes(distance_km: float, facility_type: str) -> int:
    """The app's haversineEtaMinutes, per vehicle type (matching.ts)."""
    speed = _SPEED_KMPH.get(facility_type, 40.0)
    return round(distance_km / speed * 60 + _ETA_BUFFER_MIN)


async def route_eta_minutes(origin: tuple[float, float], destination: tuple[float, float]) -> Optional[int]:
    """Reuse the app's REAL traffic-aware Google Routes ETA (/api/routes/matrix,
    single origin) — the SAME drive-time source the browser uses for hospital /
    police. Returns whole minutes, or None if routes is unavailable (no server key,
    error, empty) so the caller falls back to haversine exactly as the browser does."""
    body = {"origins": [{"lat": origin[0], "lng": origin[1]}],
            "destination": {"lat": destination[0], "lng": destination[1]}}
    data = await _send("POST", f"{config.APP_BASE_URL}/api/routes/matrix", json=body, label="routes")
    if not isinstance(data, dict) or data.get("source") == "no_key":
        return None
    results = data.get("results") or []
    if not results:
        return None
    sec = results[0].get("durationSec")
    return max(1, round(sec / 60)) if sec else None


async def _eta_for(facility_type: str, facility_point: tuple[float, float],
                   incident_point: tuple[float, float], distance_km: float) -> int:
    """One ETA number, sourced exactly as the browser sources it for this type:
    live Google Routes for hospital/police (haversine fallback), the app's per-type
    haversine for the synthetic ambulance/fire/tow posts."""
    if facility_type in _ROUTE_ETA_TYPES:
        eta = await route_eta_minutes(facility_point, incident_point)
        if eta is not None:
            return eta
    return _haversine_eta_minutes(distance_km, facility_type)


# A caller says a WHOLE sentence ("I am injured near Malviya Nagar" / "मैं मालवीय
# नगर के पास घायल हूँ"). Nominatim can't pull the place out of a sentence — only a
# bare place name resolves — so we strip these self-reference / injury / preposition
# filler words first and geocode what's left. English + Hindi (Devanagari).
_LANDMARK_STOPWORDS = {
    # English
    "i", "im", "i'm", "am", "is", "are", "was", "were", "the", "a", "an", "my", "me",
    "we", "us", "injured", "hurt", "help", "please", "near", "nearby", "at", "by",
    "to", "on", "in", "of", "close", "here", "there", "around", "somewhere", "side",
    "accident", "crash", "stuck", "stranded", "phas", "location",
    # Hindi (Devanagari)
    "मैं", "मुझे", "मेरे", "मेरी", "मेरा", "हम", "के", "की", "का", "को", "पास", "घायल",
    "हूँ", "हूं", "है", "हैं", "था", "थी", "यहाँ", "यहां", "वहाँ", "पर", "में", "और",
    "चोट", "लगी", "लगा", "हुई", "हुआ", "हो", "गया", "गई", "नज़दीक", "नजदीक", "करीब",
    "कृपया", "एक्सीडेंट", "दुर्घटना", "मदद", "फँसा", "फंसा", "फँसे", "आस", "पड़ोस",
    "लोकेशन", "जगह",
}


def _clean_landmark(text: str) -> str:
    """Drop filler/self-reference words, leaving the place-name tokens Nominatim
    can actually resolve. Devanagari-aware (mirrors the project's tokenizer)."""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']*|[ऀ-ॿ]+", text or "")
    kept = [t for t in tokens if t.lower() not in _LANDMARK_STOPWORDS]
    return " ".join(kept).strip()


# Google Places Text Search (New) — the SAME server key the app uses. Far better
# than Nominatim at Indian business / colloquial landmark queries ("Malviya Nagar
# KFC", "मालवीय नगर मेन मार्केट"). Biased (softly) to the Delhi–Dehradun corridor.
_PLACES_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
_PLACES_BIAS = {"rectangle": {"low": {"latitude": 28.3, "longitude": 76.7},
                              "high": {"latitude": 30.5, "longitude": 78.5}}}


async def _google_geocode(q: str) -> Optional[dict]:
    key = config.GOOGLE_MAPS_SERVER_KEY
    if not key or len(q) < 3:
        return None
    body = {"textQuery": q, "locationBias": _PLACES_BIAS, "maxResultCount": 1, "regionCode": "IN"}
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": key,
               "X-Goog-FieldMask": "places.location,places.displayName,places.formattedAddress"}
    data = await _send("POST", _PLACES_TEXT_URL, json=body, headers=headers, label="places")
    if not isinstance(data, dict):
        return None
    places = data.get("places") or []
    if not places:
        return None
    loc = places[0].get("location") or {}
    lat, lng = loc.get("latitude"), loc.get("longitude")
    if lat is None or lng is None:
        return None
    label = places[0].get("formattedAddress") or (places[0].get("displayName") or {}).get("text") or q
    return {"lat": float(lat), "lng": float(lng), "label": str(label)[:120]}


async def _nominatim_geocode(q: str) -> Optional[dict]:
    if len(q) < 3:
        return None
    params = {"format": "json", "q": q, "limit": "1", "viewbox": _CORRIDOR_VIEWBOX, "bounded": "1"}
    rows = await _send("GET", f"{config.NOMINATIM_URL}/search", params=params,
                       headers={"User-Agent": "TransportSahayak/1.0 (1033 helpline)"}, label="geocode")
    if not isinstance(rows, list) or not rows:
        return None
    top = rows[0]
    try:
        return {"lat": float(top["lat"]), "lng": float(top["lon"]), "label": top.get("display_name", q)[:120]}
    except Exception:
        return None


async def geocode_landmark(text: str) -> Optional[dict]:
    """Forward-geocode an APPROXIMATE spoken location to {lat,lng,label}, or None.
    Tries the filler-stripped place name first (so a full spoken sentence resolves),
    then the raw text; and for each, Google Places Text Search FIRST (best for Indian
    landmarks/businesses), then Nominatim as a keyless fallback. Deliberately
    approximate — an area/landmark centroid is enough; we never demand an address."""
    raw = (text or "").strip()
    candidates: list[str] = []
    cleaned = _clean_landmark(raw)
    if len(cleaned) >= 3:
        candidates.append(cleaned)
    if len(raw) >= 3 and raw.lower() != cleaned.lower():
        candidates.append(raw)
    for geocoder in (_google_geocode, _nominatim_geocode):
        for q in candidates:
            hit = await geocoder(q)
            if hit:
                return hit
    return None


async def fetch_responders() -> Optional[dict]:
    """GET the app's /api/aggregator/responders — the SAME payload the browser's
    useResponders consumes (registry arrays + Google `places`)."""
    data = await _send("GET", f"{config.APP_BASE_URL}/api/aggregator/responders", label="responders")
    return data if isinstance(data, dict) else None


def _candidates(responders: dict, facility_type: str) -> list[dict]:
    ft = (facility_type or "").lower()
    out: list[dict] = []
    if ft in _REGISTRY_KEY:
        for x in responders.get(_REGISTRY_KEY[ft], []) or []:
            if x.get("lat") is None or x.get("lng") is None:
                continue
            out.append({"name": x.get("name"), "lat": x["lat"], "lng": x["lng"],
                        "contact": x.get("phone") or x.get("contactNumber"),
                        "trauma": x.get("traumaLevel") is not None})
    elif ft in _PLACES_KEY:
        for g in (responders.get("places", {}) or {}).get(_PLACES_KEY[ft], []) or []:
            if g.get("lat") is None or g.get("lng") is None:
                continue
            out.append({"name": g.get("name"), "lat": g["lat"], "lng": g["lng"], "contact": g.get("phone")})
    return out


async def nearest_facility(responders: dict, facility_type: str, point: tuple[float, float],
                           capability: str = "") -> Optional[dict]:
    """Nearest facility of a type + a labelled drive-time estimate, from the app's
    own data and ETA logic. Mirrors ReportPanel.handleFacilityQuery (registry types
    + Google POIs, trauma preference for hospitals). Returns the browser `facility`
    shape: {name, contactNumber, distanceKm, etaMinutes, note}."""
    ft = (facility_type or "").lower()
    cands = _candidates(responders, ft)
    if not cands:
        return None
    pool, note = cands, None
    if ft == "hospital" and capability and any(k in capability.lower() for k in ("trauma", "head", "neuro", "spinal", "गंभीर", "सिर")):
        trauma = [c for c in cands if c.get("trauma")]
        if trauma:
            pool, note = trauma, "trauma-capable"
        else:
            note = "nearest hospital; trauma capability not confirmed"
    best = min(pool, key=lambda c: _haversine_km(point, (c["lat"], c["lng"])))
    dist = _haversine_km(point, (best["lat"], best["lng"]))
    eta = await _eta_for(ft, (best["lat"], best["lng"]), point, dist)
    return {"name": best["name"], "contactNumber": best.get("contact"),
            "distanceKm": round(dist * 10) / 10, "etaMinutes": eta, "note": note}


async def lodge_complaint(description: str, complaint_type: str, point: Optional[tuple[float, float]],
                          label: str) -> Optional[str]:
    """Log a road-defect/complaint via the app's /api/potholes (the SAME endpoint
    the browser uses) and return the real reference id, or None on failure."""
    ref = f"HD-{uuid.uuid4().hex[:6].upper()}"
    if point is None:
        return ref  # nothing to place on the map, but the reference still stands
    body = {"id": ref, "lat": point[0], "lng": point[1], "road": label or "",
            "severity": "MEDIUM", "description": f"[helpline:{complaint_type}] {description}"[:300],
            "reported_date": date.today().isoformat()}
    # Best-effort (with retries): the reference id stands even if persistence fails.
    await _send("POST", f"{config.APP_BASE_URL}/api/potholes", json=body, label="complaint")
    return ref


async def build_dispatch_update(responders: dict, point: tuple[float, float], flags: set) -> dict:
    """Compute the `services` map the closing briefing reads (the SAME shape the
    browser's dispatch_update carries): nearest ambulance/hospital/police/towing,
    plus fire when a fire/hazmat flag is set. Each = {name, etaMinutes, distanceKm},
    ETA sourced exactly as the browser sources it per type. Missing types are
    omitted; the briefing handles gaps (never invents a number)."""
    wanted = ["ambulance", "hospital", "police", "tow"]
    if "Fire" in flags or "Hazardous material" in flags:
        wanted.append("fire")
    key_map = {"ambulance": "ambulance", "hospital": "hospital", "police": "police", "tow": "towing", "fire": "fire"}
    services: dict = {}
    for ft in wanted:
        f = await nearest_facility(responders, ft, point)
        if f:
            services[key_map[ft]] = {"name": f["name"], "etaMinutes": f["etaMinutes"], "distanceKm": f["distanceKm"]}
    return services
