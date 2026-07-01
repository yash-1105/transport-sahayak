"""
dispatch.py — resolve required agencies (deterministic) + localize Police/SDRF/Forest
labels to the corridor segment. No LLM ever.
"""
import json
import os
from dataclasses import dataclass, field

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
with open(os.path.join(_DATA_DIR, "corridor_profile.json"), encoding="utf-8") as _f:
    CORRIDOR = json.load(_f)

# default human-readable labels for codes
_DEFAULT_LABELS = {
    "AMBULANCE": "Ambulance", "POLICE": "Police", "FIRE": "Fire & Rescue", "TOWING": "Towing",
    "SDRF": "SDRF", "NDRF": "NDRF", "NDMA": "NDMA", "ARMY": "Army", "FOREST_DEPT": "Forest Dept",
    "NHAI": "NHAI Maintenance", "ELECTRICITY_DEPT": "Electricity Dept", "RAILWAYS": "Railways",
    "BOMB_SQUAD": "Bomb Squad / NSG", "AERB": "AERB", "POLLUTION_CONTROL": "Pollution Control Board",
    "MUNICIPAL": "Municipal Corp", "DISTRICT_ADMIN": "District Administration",
    "HOSPITAL_ICS": "Hospital (mass-casualty)", "TUNNEL_OPERATOR": "Tunnel Operator",
    "TOLL_OPS": "Toll Operations", "PANCHAYAT_GAUSHALA": "Panchayat / Gaushala",
    "ANIMAL_HUSBANDRY": "Animal Husbandry", "CHILD_WELFARE": "Child Welfare", "FSSAI": "FSSAI",
    "IRRIGATION": "Irrigation Dept", "AGRICULTURE": "Agriculture Dept", "CONTRACTOR": "Contractor",
    "CRISIS_CENTRE": "Crisis Centre", "INTEL_BUREAU": "Intelligence Bureau",
    "MINE_RESCUE": "Mine Rescue", "NAVY_COASTGUARD": "Navy / Coast Guard", "RAF": "Rapid Action Force",
    "GOVT_TOP": "State Disaster Authority", "GAS_DETECTION": "Gas Detection Unit",
}
_LOCALIZED = {"POLICE", "SDRF", "FOREST_DEPT"}


@dataclass
class DispatchResult:
    agencies: list = field(default_factory=list)  # [{code,label}]
    dataGaps: list = field(default_factory=list)
    jurisdictionState: str = None


def _state_for_km(km):
    if km is None:
        return None
    for seg in CORRIDOR["segments"]:
        if seg["kmFrom"] <= km < seg["kmTo"]:
            return seg["state"]
    return None


def _in_wildlife_zone(km):
    if km is None:
        return False
    z = CORRIDOR["zones"]["wildlifeCorridor"]
    return z["kmFrom"] <= km <= z["kmTo"]


def resolve(record, signals, severity, location=None) -> DispatchResult:
    s = signals or {}
    codes = list(record.get("agencies", []))  # index order preserved

    def add(c):
        if c not in codes:
            codes.append(c)

    vehicles = int(s.get("vehiclesInvolved", 1) or 1)
    casualties = int(s.get("casualties", 0) or 0)

    if s.get("fire"):
        add("FIRE")
        # A vehicle that caught fire is almost never driveable afterward.
        add("TOWING")
    if s.get("hazmat"):
        add("FIRE"); add("POLLUTION_CONTROL")
    if s.get("entrapment"):
        add("SDRF")
        # Extraction always needs medical standby, regardless of what the
        # matched taxonomy record's own baseline agencies list happens to say.
        add("AMBULANCE")
        if severity.score == 4:
            add("NDRF")
    if casualties >= 20:
        add("HOSPITAL_ICS"); add("SDRF")
    if casualties >= 1:
        # Any confirmed casualty implies medical response is relevant,
        # regardless of the baseline record — some records omit AMBULANCE
        # (e.g. property-damage-only subtypes) since it's assumed irrelevant
        # until a casualty is actually reported.
        add("AMBULANCE")
    if s.get("roadBlocked"):
        add("TOWING")
    if vehicles >= 2:
        # Multi-vehicle collisions almost always leave at least one vehicle
        # immobile and obstructing the carriageway, and need traffic control
        # while it's cleared -- true across essentially every subtype, not
        # just the ones whose own baseline list happens to include TOWING/
        # POLICE. This was a real gap: a 4-vehicle collision-with-fire report
        # got AMBULANCE/POLICE/FIRE but no TOWING, even though wrecked
        # vehicles blocking the road are the norm for any multi-vehicle
        # incident, not the exception.
        add("TOWING")
        add("POLICE")
    if severity.score == 4:
        add("POLICE"); add("AMBULANCE")

    # ---- localize labels by corridor location ----
    km = (location or {}).get("km")
    state = _state_for_km(km)
    juris = CORRIDOR["jurisdictionByState"].get(state, {}) if state else {}

    agencies = []
    for c in codes:
        label = _DEFAULT_LABELS.get(c, c)
        if c in _LOCALIZED and c in juris:
            label = juris[c]
            if c == "FOREST_DEPT" and _in_wildlife_zone(km):
                label = juris["FOREST_DEPT"] + " (Rajaji wildlife corridor)"
        agencies.append({"code": c, "label": label})

    # ---- data gaps drive the structured question flow ----
    capture = record.get("capture", [])
    gaps = []
    provided_keys = {k.lower() for k, v in s.items() if v}
    for field_name in capture:
        fl = field_name.lower()
        if "location" in fl and km is None and not (location or {}).get("latlng"):
            gaps.append(field_name)
        elif "casualt" in fl and not s.get("casualties"):
            gaps.append(field_name)
        elif "no. of" in fl and not s.get("vehiclesInvolved"):
            gaps.append(field_name)
        elif fl not in provided_keys and fl.split()[0] not in provided_keys:
            gaps.append(field_name)
    # de-dup preserve order
    seen = set(); gaps = [g for g in gaps if not (g in seen or seen.add(g))]

    return DispatchResult(agencies=agencies, dataGaps=gaps, jurisdictionState=state)
