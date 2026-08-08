# Railway deploy config — Hindi voice-dispatcher latency (geographic colocation)

The Hindi Exotel phone line's per-turn latency has a large **fixed geographic
tax**: every turn makes network round-trips to Gemini (Vertex AI) and Sarvam
(Saaras STT + Bulbul TTS). If the backend and those services are on different
continents, that tax is paid twice per turn (STT + reasoning + TTS) and dominates
the mouth-to-ear budget.

All three parties should sit in/near **India**:

| Party | Region | Notes |
|-------|--------|-------|
| Caller / Exotel | India | Exotel is an Indian telephony provider. |
| Sarvam (Saaras + Bulbul) | India | `api.sarvam.ai` — Sarvam AI is India-based; endpoints serve from India. |
| Gemini text (Hindi `generate_content`) | **asia-south1 (Mumbai)** | **Code change, done** — `HINDI_VERTEX_LOCATION=asia-south1` (`dispatcher_hindi.py`). `gemini-2.5-flash` verified available there and measured markedly faster + tighter than `us-central1`. |
| Gemini **Live** (English, native-audio) | **us-central1** | **Unchanged** — the Live model may not exist in asia-south1, so English keeps its own `us-central1` client (`VERTEX_AI_LOCATION`, `dispatcher_live.py`). The two clients are independent. |
| **Railway backend** (this service) | **→ move to Singapore (`asia-southeast1`)** | **Deploy-config change (not code).** Railway's nearest region to India is Singapore. Moving the `transport-sahayak` service there cuts the Railway↔Exotel, Railway↔Sarvam, and Railway↔asia-south1 hops from transcontinental to intra-Asia. |

## How to move the Railway service region

Railway sets region per-service. In the `transport-sahayak` service → **Settings →
Region → `Southeast Asia (Singapore)`** → redeploy. (If the region control isn't
available on the plan, the alternative is recreating the service in that region with
the same env vars.) No code or env-var changes are required for the move itself.

## Env vars (region)

```bash
HINDI_VERTEX_LOCATION=asia-south1   # Hindi generate_content region (default; code change 1)
VERTEX_AI_LOCATION=us-central1      # English Gemini Live region — leave as-is
```

## Expected win

- **Gemini (Hindi reasoning):** us-central1 → asia-south1 removed a transcontinental
  round-trip. Measured off-box (US sandbox → Vertex): median **436 ms** (asia-south1)
  vs **1448 ms** (us-central1), with a much tighter tail (333–690 ms vs 590–2598 ms).
  From a Singapore-hosted backend the absolute numbers will be lower and the
  asia-south1 advantage larger.
- **Sarvam STT/TTS:** the round-trips shrink once the backend is in Singapore rather
  than the US — a per-turn saving on both the STT-finalize and TTS-first-chunk legs.

Measure the real win on Railway via the existing `[latency]` log line
(`HINDI_LATENCY_LOG=true`): compare `gemini_r0`, `tts_first_chunk`, and
`saaras_total` before vs after (a) the region code change and (b) the Railway
region move.
