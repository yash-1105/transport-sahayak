# Transport Sahayak — Step-by-Step Prompt: Rule-First Severity & Dispatch Engine
### Corridor: Delhi–Dehradun Expressway (NH-709B / NH-344G, Delhi · Uttar Pradesh · Uttarakhand)

> **How to use this file.** Paste the sections below into your coding agent (Claude Code / Cursor) **in order**, one STEP at a time. Each STEP ends with a verification CHECK (work the agent does) and a **👤 YOU DO** line (the manual action *you* take). Don't skip either. The `accident_index.json` file ships alongside this document — drop it into your repo before STEP 1.

---

## 👤 YOUR MANUAL TASKS — at a glance (do these around the agent's work)

| When | You do |
|---|---|
| Before STEP 1 | Copy `accident_index.json` into the repo at `data/accident_index.json`. |
| STEP 0 | **Decide A / B / C** (Python service vs TypeScript module vs Vercel Python fn) and tell the agent. |
| STEP 1B | Confirm the **corridor profile** values (km range, tunnel zones, wildlife zone, toll plazas) — edit the stub if needed. |
| STEP 3 | **Skim the `baseSeverity` values** in the JSON against your judgment; hand-edit any you disagree with (it's just data). |
| STEP 4 | Confirm the **state-by-location agency mapping** (Delhi/UP/Uttarakhand police, Rajaji forest, Uttarakhand SDRF). |
| STEP 6 | Set `ANTHROPIC_API_KEY` in your env / Vercel project settings (server-side only). |
| STEP 7 | Decide whether you want the frontend changes now or later (engine works without them). |
| After STEP 8 | Run the test suite, then deploy. If Option C, confirm Vercel Python runtime is enabled. |

---

## Context to give the agent first (paste once, before STEP 1)

```
PROJECT: Transport Sahayak — road-accident first-response tool for the DELHI–DEHRADUN
EXPRESSWAY (NH-709B / NH-344G / NH-307), a ~210 km NHAI access-controlled corridor that
crosses THREE states — Delhi, Uttar Pradesh (Baghpat, Baraut, Shamli, Saharanpur) and
Uttarakhand (Rajaji National Park stretch into Dehradun).

Stack: Next.js 16 App Router + TypeScript, Zustand, Google Places/Routes, Anthropic API.

CORRIDOR-SPECIFIC FACTS THAT MATTER FOR DISPATCH:
- Multi-state jurisdiction: the responding Police / SDRF / Forest unit depends on WHERE on
  the corridor the incident is. Delhi Police, UP Police, or Uttarakhand Police; Uttarakhand
  has its own SDRF for the hill/forest stretch.
- A 12 km elevated WILDLIFE CORRIDOR through Rajaji National Park / Tiger Reserve near
  Dehradun — elephant & leopard strikes are a live risk; FOREST_DEPT here = Rajaji /
  Uttarakhand Forest (Shivalik Forest Division).
- TUNNELS near Dehradun: a 2.32 km twin-tube tunnel and a 340 m Datkali tunnel — the
  "Tunnel Incidents" category (fire/CO/collapse/blockage) is real for this corridor.
- 5 toll plazas; ~20,000–30,000 vehicles/day; speed up to 100 km/h.

WHAT WE ARE CHANGING AND WHY:
Today the incident flow is LLM-FIRST: every incident is POSTed to /api/assess -> Claude
Sonnet 4.6 for a severity score, with a rule-based heuristic only as a fallback when the
API key is missing or rate-limited. That is backwards and expensive — we pay an LLM call
for incidents that a lookup table answers deterministically.

We are inverting it to RULE-FIRST:
  1. A deterministic rule engine classifies the accident, computes severity, and resolves
     which agencies to dispatch — using a fixed knowledge base of 470 accident sub-types
     (accident_index.json), a generic Indian-expressway taxonomy that applies unchanged to
     this corridor.
  2. The LLM is called ONLY when the rule engine cannot confidently classify free-text
     input, OR when signals conflict. Even then the LLM does ONE cheap job: map free text
     to {category, subType}. It NEVER computes severity or picks agencies — rules do that
     deterministically on whatever the LLM returns.

This directly fixes the operational failures our call-audit data shows:
"No Severity Assessment", "No Impact Assessment", "Unstructured Question Flow", "Time Loss".

HARD CONSTRAINTS (do not violate — existing project rules):
- No fake real-time data. The engine outputs assessments and a *required-agency list only*.
  It must NEVER fabricate ETAs, crew acknowledgement, or en-route status.
- The engine produces a NOTIFICATION/dispatch recommendation, not a tracking claim.
- Keep the existing two-Google-key separation and all current API boundaries intact.
```

---

## STEP 0 — Decide the runtime (read, then confirm)

```
The product overview says "make a python model", but the app deploys on Vercel as Next.js.
Two viable shapes — tell me which to build before writing code:

  OPTION A (Python microservice): FastAPI service `severity-engine/` exposing POST /classify.
    Next.js /api/assess calls it server-side. Pros: literal Python, reusable, easy tests.
    Cons: a second deployable, extra network hop.

  OPTION B (in-process TypeScript port): pure module `src/lib/severity/` imported directly by
    /api/assess. Zero extra infra, lowest latency, cheapest. Cons: not literally Python.

  OPTION C (Vercel Python Serverless Function, same repo): `api/classify.py` on Vercel's
    Python runtime; Next.js calls it. Keeps Python AND one deploy. Cons: cold starts.

Default: OPTION C if "must be Python" is firm; OPTION B if "cheap & simple" wins. Engine
logic is identical in all three — only the wrapper differs.
```
**👤 YOU DO:** Pick **A / B / C** and tell the agent. (Steps below are written for Option C / Python; for B the agent ports the same functions to TypeScript — the data file and rules are language-agnostic.)

---

## STEP 1 — Drop in the knowledge base & lock its shape

```
Add the provided file `accident_index.json` to the repo at: data/accident_index.json

470 records. Each record has EXACTLY this shape:
{
  "category":     string,   // one of 50 top-level categories
  "subType":      string,   // leaf, e.g. "LPG / CNG Tanker Fire – BLEVE Risk"
  "cause":        string,   // typical cause text (reference only)
  "capture":      string[], // the "Data to Capture" fields for this incident type
  "agencies":     string[], // canonical agency codes (vocabulary below)
  "baseSeverity": 1|2|3|4    // 1=LOW 2=MEDIUM 3=HIGH 4=CRITICAL
}

CANONICAL AGENCY VOCABULARY (34 codes — never invent new ones at runtime):
AMBULANCE, POLICE, FIRE, TOWING, SDRF, NDRF, NDMA, ARMY, FOREST_DEPT, NHAI,
ELECTRICITY_DEPT, RAILWAYS, BOMB_SQUAD, AERB, POLLUTION_CONTROL, MUNICIPAL,
DISTRICT_ADMIN, HOSPITAL_ICS, TUNNEL_OPERATOR, TOLL_OPS, PANCHAYAT_GAUSHALA,
ANIMAL_HUSBANDRY, CHILD_WELFARE, FSSAI, IRRIGATION, AGRICULTURE, CONTRACTOR,
CRISIS_CENTRE, INTEL_BUREAU, MINE_RESCUE, NAVY_COASTGUARD, RAF, GOVT_TOP, GAS_DETECTION

CHECK: write a one-off script that asserts len == 470, every record has all 6 keys, every
agency is in the vocabulary, baseSeverity ∈ {1,2,3,4}. Print PASS/FAIL. Don't continue until PASS.
```
**👤 YOU DO:** Copy the file into `data/` before running the agent on this step. Eyeball the PASS output.

---

## STEP 1B — Corridor profile (NEW — the only location-aware config)

```
Create data/corridor_profile.json describing the Delhi–Dehradun corridor. The SEVERITY engine
is location-agnostic; this profile is used ONLY to (a) resolve which jurisdiction's Police /
SDRF / Forest unit to name in dispatch, and (b) flag corridor-relevant zones. Stub to confirm:

{
  "corridorId": "DEL-DDN-NH709B",
  "totalKm": 210,
  "segments": [
    { "name": "Delhi (Akshardham–EPE)",      "kmFrom": 0,   "kmTo": 32,  "state": "DELHI" },
    { "name": "UP (Baghpat–Saharanpur)",      "kmFrom": 32,  "kmTo": 168, "state": "UP" },
    { "name": "Uttarakhand (Rajaji–Dehradun)","kmFrom": 168, "kmTo": 210, "state": "UTTARAKHAND" }
  ],
  "zones": {
    "wildlifeCorridor": { "kmFrom": 190, "kmTo": 202, "note": "Rajaji NP elevated section — elephant/leopard risk" },
    "tunnels": [
      { "name": "Twin-tube tunnel", "kmApprox": 203 },
      { "name": "Datkali tunnel",   "kmApprox": 207 }
    ],
    "tollPlazas": 5
  },
  "jurisdictionByState": {
    "DELHI":       { "POLICE": "Delhi Traffic Police",        "SDRF": "NDRF (Delhi)",          "FOREST_DEPT": "Delhi Forest Dept" },
    "UP":          { "POLICE": "UP Police (Highway)",         "SDRF": "UP SDRF",               "FOREST_DEPT": "UP Forest Dept" },
    "UTTARAKHAND": { "POLICE": "Uttarakhand Police (Highway)","SDRF": "Uttarakhand SDRF",      "FOREST_DEPT": "Rajaji / Uttarakhand Forest (Shivalik Div.)" }
  }
}

The km/segment boundaries are approximate placeholders — they must be confirmed by a human.
```
**👤 YOU DO:** Confirm or correct the km ranges, zone boundaries, and the jurisdiction names. (These are my best estimates from public route data — verify against the official corridor chainage before production.)

---

## STEP 2 — Build the classifier (free-text & dropdown → matched record)

```
Create severity_engine/classifier.py: classify(incident) -> ClassificationResult
incident = { subType?:str, category?:str, description?:str, language?:'en'|'hi'|'as' }

PRIORITY ORDER:
  1. EXACT SELECTION (confidence 1.0, source "operator"): incident.subType matches a record
     subType exactly (case-insensitive, trim). Common path — UI gives a searchable dropdown of
     the 470 types. NO LLM. Done.
  2. KEYWORD MATCH on description (source "rules"): build a keyword index ONCE at load — tokenize
     subType+cause to lowercase, drop stopwords + generic words (collision/crash/strike/accident/
     situations). Score records by distinct keyword hits, subType hits weighted 2x cause hits.
     HARD-OVERRIDE tokens pin a category regardless of weak scores:
        explosion/IED/bomb            -> "Brawl / Human Conflict" or "Hazardous Material"
        bleve / (lpg|cng)+fire        -> "Fire Situations" (BLEVE record)
        tanker + (spill|leak|chemical|acid) -> "Hazardous Material"
        collapse + (bridge|flyover|tunnel)  -> "Structural / Catastrophic"
        flood/submerged/swept         -> "Flood / Water Emergency"
        landslide/rockfall/boulder    -> "Landslide / Cliff Fall"
        elephant/leopard/cattle/animal -> "Vehicle to Animal"   <-- common in Rajaji stretch
        pedestrian/cyclist/child on road -> "Vehicle to Person"
        cardiac/stroke/childbirth/anaphylaxis/overdose -> "Driver / Passenger Medical"
        tunnel + (fire|smoke|trapped|co|dark) -> "Tunnel Incidents"  <-- real for this corridor
     Normalize top score to confidence in [0,1] (e.g. top/(top+runnerUp+1)).
  3. DECIDE: confidence >= 0.62 AND one dominant category -> accept (source "rules").
     Else -> source "needs_llm"; return the top 3 candidate records as a shortlist.

Return ClassificationResult { record|None, confidence:float, source, candidates:[≤3] }

CHECK (must pass WITHOUT any LLM):
  - subType "Rear-End Collision"                -> source operator, baseSeverity 2
  - "lpg tanker on fire near km 40"             -> Fire Situations BLEVE, sev 4
  - "elephant on the road in rajaji stretch"    -> Vehicle to Animal, source rules
  - "smoke filling the tunnel cars stuck"       -> Tunnel Incidents, source rules
  - "weird thing happened on the road"          -> source needs_llm (low confidence)
```
**👤 YOU DO:** Nothing manual — just confirm the CHECK cases pass.

---

## STEP 3 — Build the severity calculator (deterministic, no LLM ever)

```
Create severity_engine/severity.py: compute(record, signals) -> SeverityResult
signals (all optional, validated):
  { casualties:int=0, fatalities:int=0, fire:bool=False, hazmat:bool=False,
    entrapment:bool=False, roadBlocked:bool=False, vulnerableVictim:bool=False,
    vehiclesInvolved:int=1 }

ALGORITHM (start from record.baseSeverity, additive modifiers, then HARD OVERRIDES, then clamp 1–4):
  score = baseSeverity
  if casualties in 3..5 or vehiclesInvolved in 3..4: score += 1
  if fire: score += 1
  if entrapment: score += 1
  if vulnerableVictim: score += 1
  if hazmat: score = max(score, 3)
  HARD OVERRIDES -> force 4 (CRITICAL):
    casualties >= 20  OR  fatalities >= 5
    subType contains BLEVE / "full collapse" / "Mass Casualty (50+"
    hazmat AND agencies imply unknown chemical/gas (GAS_DETECTION/BOMB_SQUAD/AERB present)
  score = clamp(score, 1, 4)
Label: 1 LOW · 2 MEDIUM · 3 HIGH · 4 CRITICAL.
Also emit FACTUAL impactNote from signals (e.g. "3–5 casualties; fire reported; road blocked").
NEVER write ETAs or crew status. Return { score, label, impactNote, appliedModifiers:[] }.

CHECK:
  - Rear-End, 0 casualties           -> MEDIUM (2)
  - BLEVE record, any signals        -> CRITICAL (4) via override
  - any record, casualties = 25      -> CRITICAL (4) via override
  - Rear-End + 4 casualties + fire    -> verify the clamp math (2+1+1=4 -> CRITICAL) and set the
                                         test expectation to match THIS spec, not a guessed value
```
**👤 YOU DO:** Open `accident_index.json` and skim the `baseSeverity` column against your own judgment for the incident types you care most about (wildlife strikes, tunnel events, pile-ups). Hand-edit any you disagree with — it's pure data, no code changes needed.

---

## STEP 4 — Dispatch resolver + state-aware agency labels (deterministic, no LLM ever)

```
Create severity_engine/dispatch.py: resolve(record, signals, severity, location?) -> DispatchResult

Start from the union of record.agencies (already ordered by the index's priority numbering),
then add CONDITIONAL agencies driven by signals (only if absent):
    fire          -> FIRE
    hazmat        -> FIRE, POLLUTION_CONTROL
    entrapment    -> SDRF (add NDRF only if severity == 4)
    casualties>=20-> HOSPITAL_ICS, SDRF
    roadBlocked   -> TOWING
    severity == 4 -> ensure POLICE and AMBULANCE present
Preserve index order first, append conditionals after, de-dupe.

STATE-AWARE LABELING (corridor-specific, uses corridor_profile.json):
  If `location` carries a corridor km or lat/lng, map it to a segment -> state, then for the
  codes POLICE / SDRF / FOREST_DEPT attach the human-readable jurisdiction label from
  jurisdictionByState (e.g. POLICE -> "Uttarakhand Police (Highway)" in the Dehradun segment;
  FOREST_DEPT -> "Rajaji / Uttarakhand Forest" inside the wildlifeCorridor zone). The CODE
  stays canonical; only the display label is localized. If location is unknown, return codes
  with a generic label and add to dataGaps.

Also compute `dataGaps`: which of record.capture[] the operator has NOT yet supplied. This
drives the structured question flow (fixes "Unstructured Question Flow").

Return { agencies:[{code,label}], dataGaps:[] }

CHECK:
  - Head-On + roadBlocked -> AMBULANCE, POLICE, TOWING (no dupes)
  - any record + hazmat   -> FIRE and POLLUTION_CONTROL present
  - Elephant strike at km 196 -> FOREST_DEPT labelled "Rajaji / Uttarakhand Forest", POLICE labelled "Uttarakhand Police"
  - location unknown      -> generic labels + "exact location" appears in dataGaps
```
**👤 YOU DO:** Confirm the `jurisdictionByState` labels in `corridor_profile.json` read correctly for each state segment.

---

## STEP 5 — Orchestrator + the *narrow* LLM escalation

```
Create severity_engine/engine.py: assess(incident, signals, location?) -> Assessment

FLOW:
  1. result = classify(incident)
  2. ESCALATE TO LLM ONLY IF either:
       (a) result.source == "needs_llm", OR
       (b) CONFLICT GUARD: operator-selected type disagrees with description's hard-override
           tokens (e.g. picked "Rear-End Collision" but text says "tanker exploded"). Re-run the
           override-token scan on description; if implied category != result.record.category -> escalate.
     In ALL other cases: DO NOT CALL THE LLM. Proceed with result.record.
  3. LLM CALL (cheap, classification-only — the whole point):
       - Model: claude-haiku-4-5 (recommend Haiku here for cost) or claude-sonnet-4-6.
       - Send the MINIMUM: description (truncate ~400 chars) + the shortlist of candidate
         {category, subType} pairs (or the 50 category names if none). NOTHING else. No severity
         instructions, no history.
       - Force JSON-only: {"subType":"...","category":"..."} chosen from the list. max_tokens ~60,
         hard timeout ~4s.
       - Look the returned subType back up in accident_index.json for the authoritative record.
         If not found, fall back to the best rules candidate. The LLM answer is ONLY a table key.
  4. sev      = severity.compute(record, signals)              # always rules
     dispatch = dispatch.resolve(record, signals, sev, location) # always rules
  5. Return Assessment {
       category, subType, severity, severityScore, impactNote, appliedModifiers,
       agencies:[{code,label}], dataGaps, classifiedBy:"operator"|"rules"|"llm",
       llmUsed:bool, confidence, jurisdictionState? }

INVARIANT (enforce in code + tests): severity and agencies are NEVER produced by the LLM. The
LLM can only change WHICH record is selected. Given a record + signals, output is 100% deterministic.

CHECK:
  - operator picks a subType   -> llmUsed == false
  - high-confidence description -> llmUsed == false
  - vague description           -> llmUsed == true, severity still computed by rules
  - print the % of a sample batch that avoided the LLM (target: the vast majority)
```
**👤 YOU DO:** Nothing manual yet — env var is set in STEP 6.

---

## STEP 6 — Expose it & re-point the existing route

```
OPTION C: create api/classify.py (Vercel Python runtime) wrapping engine.assess(); accept
JSON { incident, signals, location }, return Assessment JSON.

EDIT src/app/api/assess/route.ts — INVERT the order:
  1. Call the rule engine FIRST with {incident, signals, location}.
  2. The engine internally decides whether to consult the LLM (STEP 5). The route no longer
     calls Anthropic directly for the common path.
  3. Return the engine's Assessment; KEEP the old response keys the frontend reads, ADD the new
     ones (agencies[], dataGaps[], appliedModifiers[], classifiedBy, llmUsed).
  4. DELETE the old "LLM first, heuristic fallback" branch — the engine IS the heuristic now.
  5. ANTHROPIC_API_KEY stays server-side only. If the key is missing AND the engine wants to
     escalate, skip the LLM and return the best rules candidate with classifiedBy:"rules" and a
     lowConfidence:true flag rather than failing.

CHECK: hit /api/assess with (a) a selected subType and (b) a vague description; confirm (a) makes
ZERO Anthropic calls (watch logs) and (b) makes exactly one short classification call.
```
**👤 YOU DO:** Add `ANTHROPIC_API_KEY` to your local `.env` **and** to the Vercel project's Environment Variables (server-side, not `NEXT_PUBLIC_`). If Option C, enable the Vercel Python runtime for the `api/` function.

---

## STEP 7 — Frontend: structured intake + transparency (recommended, optional)

```
  1. Replace free-text-only entry with a searchable SubType dropdown (470 types grouped by the
     50 categories) + keep free-text/voice as fallback routed through classification. Selecting a
     subType is the zero-LLM happy path.
  2. After assessment render: Severity badge (LOW/MEDIUM/HIGH/CRITICAL), impactNote, agency chips
     (with the state-localized labels), and a "why" expander showing appliedModifiers[].
  3. Render dataGaps[] as an ordered checklist — this becomes the structured question flow that
     replaces ad-hoc questioning.
  4. Add a badge: "Assessed by: rules" / "Assessed by: AI (low-confidence input)" so it's visible
     when AI was even consulted. Honesty is a project value.
Keep the amber "sample" banners and all dispatch-is-notification-only language.
```
**👤 YOU DO:** Decide if you want these UI changes in this pass or a later one — the engine and API work fully without them.

---

## STEP 8 — Guardrail tests (commit these)

```
Add tests asserting the cost + determinism guarantees:
  - Batch of 50 incidents, 45 with a selected subType + 5 vague free-text -> at most 5 LLM calls
    (assert via a mocked call-counter).
  - Same record + same signals -> identical severity + agencies over 100 runs (no nondeterminism).
  - ANTHROPIC_API_KEY unset -> no path throws; vague inputs degrade to rules + lowConfidence.
  - Snapshot 10 end-to-end incidents vs expected {severity, agencies}: Head-On, BLEVE,
    pedestrian strike, flood-trapped, ELEPHANT STRIKE (Rajaji km 196), TUNNEL FIRE,
    cardiac-at-wheel, mass-casualty 25, hazmat-unknown, bridge-collapse.
```
**👤 YOU DO:** Run the suite locally, then deploy. Spot-check one real-looking incident end-to-end in the deployed app.

---

## Why this design is cheap, honest, and defensible (one paragraph for your write-up)

The 470-row index is a closed, government-style decision table, so classification + severity +
dispatch are a *lookup with modifiers*, not a reasoning problem — exactly what rules do best and
LLMs do expensively. The LLM is demoted to a last-resort, classification-only fallback that fires
only on genuinely ambiguous free text and returns a tiny JSON key into the same table, so severity
and agency decisions stay deterministic, auditable (appliedModifiers), and identical whether or not
AI was consulted. The only corridor-specific layer is jurisdiction labeling — mapping a km/coordinate
on the Delhi–Dehradun route to the right state's Police / SDRF / Forest unit (Rajaji forest and
Uttarakhand SDRF in the wildlife-corridor and tunnel stretch). That keeps per-incident cost near zero
for the common path, satisfies the project's "no fake data / honesty" constraints, and closes the four
failures in the call audit: instant Severity Assessment, an Impact note, a deterministic Question Flow
from dataGaps, and the elimination of Time Loss from unstructured calls.
