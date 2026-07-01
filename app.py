"""
app.py — FastAPI wrapper around the rule-first severity engine.

Run locally (for tomorrow's demo):
    pip install -r requirements.txt
    uvicorn app:app --reload --port 8000

Your existing Next.js /api/assess route calls POST http://localhost:8000/assess.
No new deployment required — this runs alongside your POC on localhost.
"""
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from severity_engine import engine
from severity_engine.classifier import (
    INDEX,
    _CATEGORY_MAP,
    get_categories as _get_categories,
    get_subtypes_for as _get_subtypes_for,
    guess as _clf_guess,
)

app = FastAPI(title="Transport Sahayak — Rule-First Severity Engine", version="1.0")

# allow the local Next.js dev server to call this during the demo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Signals(BaseModel):
    casualties: int = 0
    fatalities: int = 0
    fire: bool = False
    hazmat: bool = False
    entrapment: bool = False
    roadBlocked: bool = False
    vulnerableVictim: bool = False
    vehiclesInvolved: int = 1


class Location(BaseModel):
    km: Optional[float] = None
    latlng: Optional[list] = None


class Incident(BaseModel):
    subType: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = "en"


class AssessRequest(BaseModel):
    incident: Incident
    signals: Signals = Signals()
    location: Optional[Location] = None


@app.get("/health")
def health():
    return {"ok": True, "records": len(INDEX)}


@app.get("/debug/gemini")
def debug_gemini():
    """
    Diagnostic only — surfaces the REAL error from a live Gemini call (bad key,
    quota, billing not enabled, etc.) instead of the silent None the actual
    assess() pipeline returns on failure by design. Safe to leave deployed:
    makes one trivial, cheap call, never touches incident data.
    """
    from severity_engine.gemini_client import gemini_health_check
    ok, detail = gemini_health_check()
    return {"ok": ok, "detail": detail}


@app.get("/subtypes")
def subtypes():
    """Every sub-type with its curated category (deduped by subType string)."""
    seen: set[str] = set()
    result = []
    for r in INDEX:
        st = r["subType"]
        if st not in seen:
            seen.add(st)
            result.append({"category": _CATEGORY_MAP.get(st, "Other"), "subType": st})
    return result


@app.get("/categories")
def categories():
    """Return [{category, count}] sorted descending by count."""
    return _get_categories()


@app.get("/categories/subtypes")
def category_subtypes(name: str):
    """Return sorted list of subType strings in this category (name as query param to avoid slash routing issues)."""
    return _get_subtypes_for(name)


class GuessRequest(BaseModel):
    description: str


@app.post("/guess")
def guess_endpoint(req: GuessRequest):
    """Classify a free-text description; returns a confirm-card payload."""
    return _clf_guess(req.description)


@app.post("/assess")
def assess(req: AssessRequest):
    return engine.assess(
        req.incident.model_dump(),
        req.signals.model_dump(),
        req.location.model_dump() if req.location else None,
    )
