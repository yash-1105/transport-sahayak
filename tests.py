"""Guardrail tests — determinism + cost. Run: python tests.py"""
from severity_engine import engine, classifier, severity

def check(name, cond):
    print(("PASS" if cond else "FAIL"), "-", name)
    assert cond, name

# determinism: same input -> identical output 100x
a = engine.assess({"subType": "Head-On Collision"}, {"roadBlocked": True}, {"km": 196})
for _ in range(100):
    b = engine.assess({"subType": "Head-On Collision"}, {"roadBlocked": True}, {"km": 196})
    assert a == b
check("deterministic over 100 runs", True)

# operator selection never uses LLM
o = engine.assess({"subType": "Rear-End Collision"}, {}, {"km": 10})
check("operator dropdown -> no LLM", o["llmUsed"] is False and o["severity"] == "MEDIUM")

# hard override -> CRITICAL
o = engine.assess({"subType": "Rear-End Collision"}, {"casualties": 25}, None)
check("25 casualties -> CRITICAL override", o["severity"] == "CRITICAL")

# BLEVE always critical
rec = classifier._find_exact("LPG / CNG Tanker Fire \u2013 BLEVE Risk")
check("BLEVE record present", rec is not None)
s = severity.compute(rec, {})
check("BLEVE -> CRITICAL", s.label == "CRITICAL")

# hazmat floors at HIGH
o = engine.assess({"subType": "Side-Swipe Collision"}, {"hazmat": True}, None)
check("hazmat floors >= HIGH", o["severityScore"] >= 3)

# state labeling
o = engine.assess({"subType": "Head-On Collision"}, {}, {"km": 196})
labels = " ".join(a["label"] for a in o["agencies"])
check("Uttarakhand police labeled in Dehradun segment", "Uttarakhand Police" in labels)

# cost: batch of 50, 45 selected + 5 vague, no key -> 0 actual LLM calls (key absent)
calls = 0
for i in range(45):
    if engine.assess({"subType": "Rear-End Collision"}, {}, None)["llmUsed"]:
        calls += 1
for i in range(5):
    if engine.assess({"description": "xyzzy something unclear"}, {}, None)["llmUsed"]:
        calls += 1
check("no spurious LLM calls when key absent", calls == 0)

print("\nALL TESTS PASSED")
