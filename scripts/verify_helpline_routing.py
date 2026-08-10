"""Live-simulation harness for the Hindi multi-intent helpline (manual check).

Drives HindiDispatcherSession._reason with REAL Gemini (Vertex AI, via the local
service account that severity_engine/google_credentials.py loads) for each
intent, bypassing STT/TTS and the browser. The two tools that need a frontend
round-trip (find_nearest_facility, lodge_complaint) are stubbed with canned
"frontend replies" so the model's routing can be exercised without a running
browser. It records which tools the model actually calls per intent and checks
the expected routing.

This is NOT part of the offline test suite (tests.py): it needs live Vertex
credentials and burns a handful of Gemini text calls. Run it manually to
re-verify model routing:

    python3 scripts/verify_helpline_routing.py

Credentials: resolved by severity_engine/google_credentials.py — a Railway
secret in prod, or the local service-account file for dev. No extra setup.
"""
import asyncio
import os
import sys

# Repo root is one level up from scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from severity_engine.dispatcher_hindi import HindiDispatcherSession  # noqa: E402
from severity_engine.dispatcher_live import _get_client  # noqa: E402


class FakeWS:
    async def send_json(self, payload):  # frames are irrelevant to routing
        return None


def new_session():
    s = HindiDispatcherSession.__new__(HindiDispatcherSession)
    HindiDispatcherSession.__init__(s, FakeWS())
    # Canned location so get_current_location resolves without a browser.
    s.state.location = {"lat": 26.15, "lng": 91.78, "label": "NH-27 near Ganeshguri"}

    async def fake_facility(facility_type="", capability=""):
        return {"ok": True, "needs_location": False, "facility": {
            "name": "Gauhati Medical College Hospital" if facility_type == "hospital" else f"nearest {facility_type}",
            "contactNumber": "0121-2655100", "distanceKm": 6.4, "etaMinutes": 14,
            "note": "trauma-capable" if capability else None,
        }}

    async def fake_complaint(description="", complaint_type="road_defect"):
        return {"ok": True, "reference_id": "HD-482159"}

    s._tool_find_nearest_facility = fake_facility
    s._tool_lodge_complaint = fake_complaint
    return s


async def run_scenario(title, utterances, expect_tool=None, expect_accident=False):
    s = new_session()
    client = _get_client()
    calls = []
    orig = s._dispatch_tool

    async def rec(name, args):
        calls.append(name)
        return await orig(name, args)

    s._dispatch_tool = rec

    last_reply = ""
    for u in utterances:
        s.state.caller_transcript += " " + u
        await s._apply_local_signals_from_transcript()
        last_reply = await s._reason(client, u)

    tool_ok = (expect_tool is None) or (expect_tool in calls)
    acc_ok = (s._accident_mode is bool(expect_accident))
    no_submit_ok = expect_accident or ("submit_incident" not in calls)
    ok = tool_ok and acc_ok and no_submit_ok

    print(f"\n{'PASS' if ok else 'FAIL'}  {title}")
    print(f"    utterance : {utterances[-1]}")
    print(f"    tools     : {calls or '(none)'}")
    print(f"    accident? : {s._accident_mode}  (expected {bool(expect_accident)})")
    print(f"    reply     : {last_reply[:220]}")
    if not ok:
        print(f"    !! expected tool={expect_tool!r} present={tool_ok}, accident_ok={acc_ok}, no_submit={no_submit_ok}")
    return ok


async def main():
    print("=== Hindi multi-intent helpline — live routing verification ===")
    results = []
    results.append(await run_scenario(
        "FACILITY — nearest trauma-capable hospital",
        ["यहाँ पास में सबसे नज़दीकी अस्पताल कौन सा है जो सिर की गंभीर चोट संभाल सके?"],
        expect_tool="find_nearest_facility"))
    results.append(await run_scenario(
        "ETA — how long will an ambulance take",
        ["यहाँ से नज़दीकी एम्बुलेंस को आने में कितना समय लगेगा?"],
        expect_tool="find_nearest_facility"))
    results.append(await run_scenario(
        "SCHEME/INFO — golden hour / no money for treatment",
        ["मेरे पास इलाज के पैसे नहीं हैं, क्या गोल्डन आवर योजना में मुफ़्त इलाज मिल सकता है?"],
        expect_tool="answer_info_question"))
    results.append(await run_scenario(
        "COMPLAINT — big pothole on the highway",
        ["यहाँ हाईवे पर एक बहुत बड़ा गड्ढा है, मुझे इसकी शिकायत दर्ज करानी है।"],
        expect_tool="lodge_complaint"))
    results.append(await run_scenario(
        "BREAKDOWN — bike chain broke at night (no injury)",
        ["मेरी बाइक की चेन टूट गई है और रात के ग्यारह बज रहे हैं।",
         "मुझे मैकेनिक चाहिए।"],
        expect_tool="find_nearest_facility", expect_accident=False))
    results.append(await run_scenario(
        "ACCIDENT — collision with injuries (must enter dispatch flow)",
        ["एक्सीडेंट हो गया! मेरी कार दूसरी कार से टकरा गई है, दो लोग घायल हैं।"],
        expect_tool="search_incident_type", expect_accident=True))

    print(f"\n=== {sum(results)}/{len(results)} intents routed correctly ===")


if __name__ == "__main__":
    asyncio.run(main())
