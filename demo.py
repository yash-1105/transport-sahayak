"""demo.py — runs WITHOUT FastAPI or a Gemini key. Great for a live walk-through."""
from severity_engine import engine

DEMOS = [
    ("Operator selects from dropdown (zero AI)", {"subType": "Rear-End Collision"}, {"casualties": 1}, {"km": 50}),
    ("Voice/text: LPG tanker on fire", {"description": "lpg tanker on fire near km 40"}, {"fire": True}, {"km": 40}),
    ("Elephant in Rajaji wildlife stretch", {"description": "elephant blocking the road"}, {"roadBlocked": True}, {"km": 196}),
    ("Fire inside the Dehradun tunnel", {"description": "smoke filling tunnel cars stuck inside"}, {"fire": True, "entrapment": True}, {"km": 203}),
    ("Mass-casualty pile-up", {"subType": "Multi-Vehicle Pile-Up (5\u201310 vehicles)"}, {"casualties": 25, "fire": True, "roadBlocked": True}, {"km": 120}),
]

for title, inc, sig, loc in DEMOS:
    o = engine.assess(inc, sig, loc)
    print("\n" + "=" * 70)
    print(title)
    print(f"  Incident : {o['subType']}")
    print(f"  Severity : {o['severity']} ({o['severityScore']}/4)")
    print(f"  Impact   : {o['impactNote']}")
    print(f"  Dispatch : {', '.join(a['label'] for a in o['agencies'])}")
    print(f"  Ask next : {', '.join(o['dataGaps']) or '-'}")
    print(f"  AI used  : {o['llmUsed']}  (classified by: {o['classifiedBy']})")
