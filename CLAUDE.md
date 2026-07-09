@AGENTS.md

# Transport Sahayak — Project Context for Claude

**What this is:** A road-accident first-response proof-of-concept for the Assam Transport Department. Honesty over impressiveness — nothing is faked.

---

## Hard Rules — never violate these

| # | Rule |
|---|------|
| 1 | **No fake real-time data presented as real.** No live ambulance GPS, no fake ETAs from invented traffic, no auto-escalation timers. Exception: a clearly-labelled **simulated** ambulance marker may animate along the actual highlighted route as a visual demo aid, as long as it is unmistakably tagged "Simulated" in the UI and never implies real vehicle tracking. |
| 2 | **Google traffic ETAs are allowed** — labelled exactly as `"Est. drive time from [Facility], current traffic — vehicle leaving now."` Never as `"ambulance arriving in X."` We do not track ambulances. |
| 3 | **No phantom infrastructure.** If a feature needs field equipment that doesn't exist (GPS terminals, in-vehicle radios), don't build it. |
| 4 | **Sample data must be labelled** — in code (`"sample": true` on every record) AND in the UI (amber banner on all four synthetic layers). |
| 5 | **Dispatch alert = notification record only.** Who was notified, what was sent, when. No acknowledgement, crew status, or en-route status ever implied. |
| 6 | **Google Places data is never persisted** beyond place IDs (Google ToS §3.2.4). All place details fetched on demand with `cache: "no-store"`. |

---

## Tech Stack

| Concern | Choice |
|---------|--------|
| Framework | Next.js 16 App Router + TypeScript |
| Styling | Tailwind CSS v4 |
| Map | `@vis.gl/react-google-maps` + Google Maps JS API (`ssr: false` dynamic import via `MapLoader.tsx`) |
| POI data | Google Places API (New) — live hospitals, police, mechanics, pharmacies, fuel |
| Routing / drive times | Google Routes API (New) — `computeRouteMatrix` + `computeRoutes`, `TRAFFIC_AWARE` |
| Reverse geocoding | Nominatim / OpenStreetMap (public endpoint, no key needed) |
| Voice input | Browser Web Speech API — `en-IN`, `hi-IN`, `as-IN` locales |
| AI severity | Anthropic Messages API — `claude-sonnet-4-6` via `/api/assess` route handler |
| State | Zustand — `useEventLog` (append-only), `useRoutingStore` (polylines), `useLocaleStore` (i18n) |
| i18n | Flat string map `src/i18n/strings.ts` — EN / HI / AS, session-persisted via `sessionStorage` |

---

## Voice Dispatcher — Python Backend (`app.py` + `severity_engine/`)

A separate FastAPI service (deployed on Railway, not Vercel) backs the "Voice" tab's conversational dispatcher at `/ws/dispatcher`, plus the plain speech-to-text tab at `/ws/voice` (Google Chirp — untouched by any of this). **English and Hindi run on two completely different pipelines** — `app.py`'s `/ws/dispatcher` route picks one by `?locale=`:

| Locale | STT | Reasoning | TTS | File |
|--------|-----|-----------|-----|------|
| `en-IN` | Gemini Live (built-in) | Gemini Live (built-in, native-audio) | Gemini Live (built-in) | `severity_engine/dispatcher_live.py` |
| `hi-IN` | Sarvam **Saaras v3** (streaming WS) | Plain Gemini `generate_content` (Vertex, text-only, no Gemini Live) | Sarvam **Bulbul v3** (streaming WS) | `severity_engine/dispatcher_hindi.py` |

`HindiDispatcherSession` **subclasses** `DispatcherSession` — it reuses the exact same tool handlers (`search_incident_type` with the vehicle-pair override so "कार ट्रक से टकराई" never records as Car vs. Car, `update_form_field`'s taxonomy validation, `submit_incident`'s hard-gated required fields), the same `DispatcherState`, the same deterministic `next_question` sequencing, and the same browser-facing WebSocket protocol (`ready` / `status` / `form_update` / `request_location` / `submitted` / `turn_complete` / `transcript` / binary PCM audio out). Only the audio/reasoning transport differs. **Never edit `dispatcher_live.py` (or its English system prompt) to fix a Hindi issue** — Hindi-only behavior belongs in `dispatcher_hindi.py`'s own system prompt.

Sarvam client code lives in `severity_engine/sarvam_speech.py` (raw WebSocket clients, no `sarvamai` SDK dependency — `websockets` is already a `uvicorn[standard]` transitive dep). Saaras runs with server-side VAD (`vad_signals=true`); each `{"type":"data"}` message is a **final** transcript for one detected utterance segment (no interim/partial results in this mode). Bulbul is configured for `linear16` output at 24 kHz — the same rate the frontend's Gemini-Live playback path already expects, so `useVoiceDispatcher.ts` needed no new audio-decoding path, only a Hindi-specific `playbackRate` of `1.0` (vs. Gemini Live's `0.88` slowdown, which is a Gemini-Live-specific compensation, not a general one).

If Bulbul synthesis fails mid-call, the reply is sent as a `{"type":"tts_text"}` frame and rendered as a text bubble in `DispatcherSection.tsx` instead of leaving the caller in silence — this is a documented fallback, not a bug.

### `useVoiceDispatcher.ts` must not reopen the mic until audio has actually finished PLAYING, not just finished GENERATING (English/Gemini Live)

Real reported bug (no headphones, laptop speaker): the agent classified an incident without the caller ever having spoken a word. Root cause: the frontend used to flip to `"listening"` (which is what gates the AudioWorklet's `micOpen` check and starts transmitting mic audio) the INSTANT it saw either a `"status":"listening"` event OR a bare `"turn_complete"` event (there was a second, redundant `case "turn_complete": setStatus("listening")` handler that fired unconditionally, independent of whatever the backend's own subsequent `"status"` message said). Both of those signals only mean Gemini Live finished **generating** that turn's audio server-side — they say nothing about whether the browser has finished **playing** it. Audio chunks are scheduled ahead on the Web Audio timeline (`playChunk`'s `nextStartTimeRef`), so for anything longer than a single short reply (the multi-sentence opening greeting, easily several seconds, is the worst case since it's unconditional and the very first thing that happens), there's a real window where the mic reopens while the agent's own voice is still audibly coming out of the speaker. Without headphones, the browser's built-in `echoCancellation` doesn't reliably suppress open-air speaker-to-mic bleed, so that tail of self-audio gets transmitted back into the SAME Gemini Live session — which is reactive (responds to whatever audio it receives) — and it reacted to hearing itself as if the caller had just described an incident.

Fixed in `useVoiceDispatcher.ts`'s `"status"` handler: the transition to `"listening"` specifically is now delayed by however much audio is still scheduled in the playback queue (`nextStartTimeRef.current - ctx.currentTime`, the same calculation already used for the `"call_complete"` drain-wait) plus a 250ms margin, guarded by a `statusSeqRef` sequence counter so a newer status (e.g. the very next turn already starting to speak) correctly supersedes and skips a now-stale pending mic-open rather than forcing it anyway. The redundant unconditional `turn_complete` → `"listening"` handler was removed entirely — every real `turn_complete` on the backend (`dispatcher_live.py`) is already followed by an explicit `"status"` message (`"listening"`, or `"thinking"` post-submission — see below) or a `"call_complete"`, so the frontend no longer needs (and should not have) its own independent fallback that ignores what the backend actually said. **If you touch `useVoiceDispatcher.ts`'s status handling again: never gate mic-open on a signal that only reflects server-side generation completion — always wait out whatever's still queued in `nextStartTimeRef`.** Hindi (`dispatcher_hindi.py`) was never at risk of this specific failure mode: its own `_speak_or_fallback` already computes and waits out the real TTS playback duration server-side (`playback_ends`) before ever telling the frontend to re-enter listening, so its status transitions are already synced to actual audio completion, not generation completion.

### Post-submission closing briefing (both languages — `severity_engine/dispatch_briefing.py`)

The call no longer ends at `submit_incident`. Flow: the backend sends `submitted` as before (frontend runs its EXISTING assess + MatchingPanel flow untouched) → `ReportPanel.tsx` reads the resulting `ROUTE_ESTIMATED` / `HOSPITAL_MATCHED` event-log entries (the exact numbers the dashboard cards display — **nothing recomputed, no new API call**) and sends them back over the still-open dispatcher WS as one `{"type":"dispatch_update","services":{ambulance|fire|towing|hospital|police}}` frame (debounced 2.5s in an effect) → the backend delivers ONE final turn — responder ETAs → SOP safety guidance → follow-up-call script → goodbye — then sends `{"type":"call_complete"}`, on which the frontend ends the call after draining queued playback. Key facts:

- **All briefing content is built deterministically in `dispatch_briefing.py`** (shared by both pipelines): SOP selection is rule-first from `DispatcherState` flags (bleeding → fire → unconscious/not-breathing → trapped → hazmat, max 3 specific + the general stay-calm line — the model chooses HOW to say them, never WHICH); Hindi ETA minutes are pre-rendered as words ("छब्बीस मिनट") because Bulbul reads bare digits unreliably; the closing/follow-up script lines are fixed per language. The model is instructed to use names/numbers verbatim, always as **estimates** ("approximately/लगभग"), services as **notified** — never "dispatched and tracked" (hard rules 1/2/5).
- **English**: `_brief_and_close` task (spawned at the first `turn_complete` after submit) waits on `dispatch_update` (`DISPATCH_BRIEFING_WAIT_S`, default 30s), injects the briefing as a synthetic turn into the SAME Gemini Live session, and the pump ends the call on the turn_complete that follows audio produced after the briefing (`_spoke_after_briefing` guards against a stray earlier turn_complete). A failsafe forces `call_complete` if the turn never completes (the watchdog is parked after submission, so the briefing needs its own) — **this failsafe is a STALL timeout (`DISPATCH_BRIEFING_STALL_TIMEOUT_S`, default 15s = time since the last audio chunk), not a total-length budget.** It used to be a single flat `asyncio.sleep(45)` from when the briefing turn was sent, and that cut real replies off mid-sentence in production (confirmed live: caller heard the ambulance ETA, then got cut off partway into the fire service ETA) — the full briefing (multiple responder ETAs + up to 4 SOP lines + a 6-line closing script) can legitimately run well past a minute of continuous speech, so a fixed total-time budget was never going to be long enough for every case. Now it polls once a second and only force-ends after real silence (no new chunk updating `_model_last_spoke`, the same field the general watchdog already tracks) for the stall window — length of the reply no longer matters, only genuine wedging does. Regression-tested in `tests.py` with a simulated ~20s of continuously-arriving chunks (must never cut off) and a genuine no-audio stall (must still end, no hang).
  - **The mic must stay CLOSED during the `_brief_and_close` wait window.** Real reported bug: the English agent started speaking automatically and repeatedly, asking the same thing over and over without waiting for a real reply. Root cause: `_pump_gemini_to_client` sent `{"status":"listening"}` unconditionally after every `turn_complete`, including the "please stay on the line, I'm checking" acknowledgment turn right after `submit_incident` — reopening the frontend mic gate (`useVoiceDispatcher.ts` only opens the mic on `"listening"` for en-IN) for the entire up-to-30s `dispatch_update` wait, even though the caller has nothing left to say at that point in the call. Gemini Live is reactive (only speaks in response to input, per this module's own docstring) — but ANY caller utterance or background noise picked up during that open-mic window got treated as a fresh turn, and with no new information to report yet, the model just repeated its "please hold on" line every time it heard anything at all — which the caller naturally answering ("ok"/"okay") would immediately re-trigger again, a self-reinforcing loop. Fixed by sending `{"status":"thinking"}` instead of `"listening"` specifically for this one turn_complete (the branch where `state.submitted` is true and `_briefing_task` is about to be created) — `"thinking"` is an existing status the frontend already renders correctly, and correctly keeps the mic gate closed with no frontend change needed. Regression-tested in `tests.py` with a mocked Gemini Live `receive()` stream, asserting no `"listening"` status is ever sent for this turn. Hindi's equivalent flow (`dispatcher_hindi.py`) was never at risk of this specific bug — its caller-facing loop (`while not self._ended.is_set() and not self.state.submitted:`) exits immediately once `submitted` becomes true and moves straight to `_deliver_dispatch_briefing`, so it never re-opens listening for a new utterance after submission at all.
- **Hindi**: `_deliver_dispatch_briefing` runs after the main run() loop exits on `submitted`, using the same `_agent_turn` machinery with a briefing-only gen config (`_BRIEFING_MAX_OUTPUT_TOKENS = 1200`, raised from an earlier 800 as a precaution once the English-side cutoff bug above was found — the normal 300 ceiling would truncate this one genuinely long turn; every other turn keeps the tight latency budget). Hindi's failure mode for this would be token-truncation rather than audio-cutoff (Bulbul TTS speaks a complete, already-fully-generated text with a duration-aware playback wait, unlike English's incrementally-streamed Live audio), so the same length-underestimation risk doesn't need the same stall-based fix here — only a generous enough ceiling, which a higher value can only ever help, never hurt.
- **If `dispatch_update` never arrives** (matching failed, duplicate dialog stalls it, old frontend), the briefing honestly says services have been notified and **skips ETAs entirely — never invents a number**. If the browser never gets `call_complete` (old backend), a post-submission socket close is treated as a normal call end, not a reconnectable drop (`submittedRef` in `useVoiceDispatcher.ts`).
- Announcement phrasing deliberately softened from "has been dispatched" to "has been notified — responds from [location], estimated arrival approximately N minutes" to stay inside hard rules 2 and 5; the spoken "our team will contact you within two hours" follow-up promise is per explicit user spec.

`severity_engine/local_extract.py`'s hazard-phrase lexicon includes Hindi phrases; its tokenizer must stay Devanagari-aware (`[ऀ-ॿ]+`) so Hindi negation markers (नहीं, मत, बुझ...) actually suppress false-positive hazard flags — this broke silently once already ("आग नहीं लगी" was setting Fire=true) because the tokenizer only matched `[a-z0-9]+`.

### `hindi_glossary.json` must cover common loanwords, not just "pure" Hindi vocabulary

Real reported bug: a Hindi caller who said "मेरी कार किसी दूसरी कार से टकरा गई" (my car collided with another car — using कार, the everyday English loanword, exactly as the Hindi system prompt itself explicitly says is natural and welcome) got asked to clarify the incident type instead of it being inferred, even though `search_incident_type` runs automatically on a confident match. Root cause: `_translate_hindi()`'s tokenizer only captures `[a-z0-9]+` after normalization, and **कार was never in the glossary** (only गाड़ी and वाहन mapped to "car") — so the Devanagari "कार" tokens were silently dropped entirely, leaving zero vehicle-type signal for the classifier and misclassifying it as an unrelated record (confirmed live: `classify()` fell to `"Pedestrian Strike – Hit and Run"` at confidence 0.45, below the 0.6 acceptance floor, hence `lowConfidence=True` and the agent asking the caller instead of auto-applying). Fixed by adding कार/कारें/कारों → "car" (this file is the single shared source for both `classifier.py` and `src/lib/incidentClassifier.ts`, so one fix covers both), plus standalone ऑटो → "auto" (same bug class — ऑटो alone, as opposed to the already-covered "ऑटो रिक्शा", was also being dropped). When adding a new glossary entry, check ordering doesn't collide with an existing longer phrase (सरकार containing कार is the standing regression test — `tests.py` — never let a short addition false-match inside a longer unrelated word) and re-run `tests.py`.

**The glossary fix alone wasn't enough** — reported again live after the fix shipped, same phrasing family. Root cause #2, in `classifier.classify()` itself: `"Car vs. Car Collision"`'s cause text is deliberately keyword-stuffed with "car" ("Two cars collide collided / car to car crash / another car / car hit car / ... / car accident"), so it out-scores every other record whenever "car" appears at all — but plenty of real Hindi phrasings for a car-vs-car collision still don't cross the acceptance thresholds (`top >= 4 and confidence >= 0.45`, or `top >= 2 and confidence >= 0.6`) depending on exactly which collision verb the caller used and how the TF-IDF/token-overlap math shakes out for that specific sentence — fuzzy scoring over natural language is never going to be 100% reliable for an unambiguous case that shouldn't need fuzzy scoring at all. Fixed with a second deterministic override in `dispatcher_live.py`, alongside the existing two-distinct-vehicle-type override (`_find_vehicle_pair_subtype`): `_vehicle_type_mention_counts()` counts raw (non-deduped) alias mentions per canonical vehicle type — telling "car ... car" (named twice) apart from a single passing mention ("my car broke down") that `_mentioned_vehicle_types`'s set-based dedup can't distinguish — and when some type is named twice *alongside* a collision verb (`_mentions_collision`, English + Hindi roots: collision/collided/crash/struck/rammed/hit, टक्कर/टकरा/भिड़ंत/ठोक) and the taxonomy has an "X vs. X" record for that type (`_find_same_type_subtype` — generic, checks the type's own subtype pattern matching *twice* within a subType string, so it needs no hardcoded record names and correctly no-ops for types with no same-type record, e.g. auto-rickshaw), that record wins outright at confidence 0.9, bypassing `classify()`'s fuzzy scoring entirely for this case. Applies to both languages (English's `DispatcherSession._tool_search_incident_type` is inherited byte-for-byte by Hindi's `HindiDispatcherSession`). **While testing this**, found (but deliberately did NOT fix, out of scope for what was asked) that `classify()` on its own already misclassifies plain single-car mentions with no collision language at all (e.g. "मेरी कार खराब हो गई" / "my car broke down" → `Car vs. Car Collision` at 0.52 confidence, `lowConfidence=False`) — same keyword-stuffing root cause, reproduces identically in English and predates both of today's fixes (not something either change introduced); the new same-type override's collision-verb guard correctly stays silent for it, so it doesn't make that pre-existing issue worse, but it also doesn't fix it. Regression tests for all of this live in `tests.py` alongside the existing vehicle-pair tests.

### Hindi dispatcher: latency, empathy, and barge-in (`dispatcher_hindi.py`)

- **Per-turn latency is logged**, not guessed at — every turn logs one `[latency] gemini_r0=...ms tts_first_chunk=...ms turn_total=...ms` line (toggle: `HINDI_LATENCY_LOG=false`). Measured honestly via `google-genai` on this project's real Vertex credentials: `gemini-2.5-flash` costs **~2.2–2.8s per call regardless of prompt/token-count tuning** (shrinking the system prompt ~40% and halving `max_output_tokens` did not move the number — the cost here is Vertex's fixed round-trip/generation overhead, not proportional to these short prompts/outputs). The one **deterministic, guaranteed** win is `_UTTERANCE_GRACE_S` (1.0s → 0.45s) — a fixed tax cut from every single turn. Don't assume further prompt-shrinking will reduce latency without measuring again; it didn't here. `gemini-2.5-flash-lite` was tried as a faster alternative and rejected: it returned empty `candidate.content.parts` and a `400 "Please use a valid role: user, model"` in testing that `gemini-2.5-flash` never produced — the code now defensively re-wraps model turns with an explicit `role="model"` and never returns a silently-empty reply regardless of model, but the *default* model stayed `gemini-2.5-flash` because it's the one proven reliable here. **Structural reason a normal turn "thinks" for a while**: whenever the model calls a tool (nearly every turn, since almost every turn touches `update_form_field`), `_reason()`'s loop makes 2 *sequential* Vertex round trips — one to decide the tool call(s), one after seeing the results to produce the actual spoken reply — and that second round trip is not optional: the model needs the tool response's `next_question`/`tone_reminder` to know what to say, which is precisely the mechanism that fixed the earlier "asks off-list/repeated questions" bugs (see FOLLOW-UP QUESTIONS above), so collapsing it back to one round trip would very likely reopen that regression and needs live-call verification before ever being attempted, not just changed and hoped. The `_hindi_system_prompt()` string itself was trimmed post-dispatch-briefing-feature to remove a paragraph that was being resent on every single turn of the whole call for something only spoken once at the very end — real but modest (a few hundred chars against the doc's already-established ~2.2–2.8s **fixed**, not size-proportional, per-call floor), so don't expect this alone to be the fix if latency is still the complaint. If it still isn't fast enough, the next thing to actually *try* (not just reason about) is setting `GEMINI_TEXT_MODEL=gemini-2.5-flash-lite` on Railway now that the empty-response/role-validation defenses above already exist — that's a config change, verify live before trusting it, and revert if it reproduces the original empty-response failures.
- **Barge-in (interrupting the agent's own reply) has exactly ONE reader of `SaarasStream`'s events at any moment** — `_speak_or_fallback` polls for interruption inline, in the same coroutine that streams TTS audio, rather than via a separate concurrent watcher task. An earlier two-task design (`_play_reply` + a separate `_watch_for_bargein` task both calling `get_event()`) had a real, empirically-confirmed race: whichever task "lost" the `asyncio.wait(FIRST_COMPLETED)` race had often *already* dequeued a real caller event as a side effect before being cancelled, silently corrupting or destroying the caller's next utterance. `get_event()` is a single-consumer read (like `Queue.get()`) — never give it two independent concurrent callers. If touching barge-in again, keep it to one reader.
- Barge-in arms relative to when audio actually **starts playing** (the first real TTS chunk), not to when `_speak_or_fallback` merely begins — Bulbul's connect + first-chunk network latency can itself exceed a fixed arm delay, and arming any earlier risks treating the tail of the caller's *own* preceding utterance as an interruption of audio nobody has heard yet.
- Real barge-in requires the browser to keep streaming mic audio while the agent is speaking, not just while listening — `useVoiceDispatcher.ts`'s mic-gate is `locale === "listening" || (hi-IN && "speaking")`, scoped so the added branch is structurally unreachable for `en-IN` (English's gate is unchanged: `"listening"` only, per `dispatcher_live.py`'s `NO_INTERRUPTION` design).
- The system prompt bakes in a **Hindi phrasing glossary** for the most common `next_question` hints (चोट लगी है, फँसा हुआ, आग/रिसाव, होश में, साँस, खून, वाहनों की संख्या) and an explicit reply-shape rule (acknowledge what the caller just said, in varied phrasing, before asking exactly one question) — this was added after observing the agent re-ask "क्या किसी को चोट लगी है?" after the caller had already said "दो लोग घायल हैं", and after observing mechanical "जी"/"ठीक है" openers on every turn.
- Bulbul v3 does **not** support pitch/loudness/SSML (verified against Sarvam's docs — don't add UI or config assuming otherwise). The real, tunable knobs are `pace`, `temperature`, and `min_buffer_size`/`max_chunk_length` (the latter two trade prosody smoothness for a faster time-to-first-audio-chunk) — all exposed as `SARVAM_TTS_*` env vars.
- **Verified live against the real Bulbul API** (with a real `SARVAM_API_KEY`, by measuring synthesized-audio duration) that `pace` does actually work — confirmed at extremes (0.5x/2.0x) and at small increments once `temperature` was lowered enough to stop per-call stochastic variance from swamping the signal (comparing two runs at the *default* 0.6–0.7 temperature can differ by ±10% in duration from randomness alone, not from whatever parameter you changed — don't trust a single-sample before/after comparison at normal temperature). Pace has since been iterated purely on user feedback without further live testing (1.0 → 1.15 → 1.3 → 1.2 → 1.1, current) — API credits are limited, so re-verify the pace *mechanism* live only if it's in question, not every time the *value* changes; a bare number tweak doesn't need a round-trip to Sarvam. `temperature=0.7`/`min_buffer_size=50`/`max_chunk_length=150` were chosen after a previous iteration over-indexed on latency (`pace=1.0`, small 30/90 buffers) and users reported the result sounded slow and robotic — the buffer sizes are back to Sarvam's own documented defaults on the theory that more text per synthesis segment means fewer prosody "resets"; this reasoning is sound but, unlike the pace mechanism, **not independently confirmed by ear** — nobody in this loop can listen to the output.
- **Voice/gender: `SARVAM_TTS_SPEAKER` and the system prompt's self-referential grammar must always match.** Currently `shubh` (male, per user preference after comparing voices in Sarvam's playground). The Hindi system prompt in `_hindi_system_prompt()` says "पुरुष ऑपरेटर" and uses पुल्लिंग (masculine) verb forms ("समझ रहा हूँ", "दर्ज कर रहा हूँ") — if the speaker is ever switched back to a female voice (e.g. `priya`), these must be switched back to स्त्रीलिंग ("समझ रही हूँ" etc.) in the same change, or the voice will speak grammatically mismatched Hindi (a male voice saying feminine-conjugated verbs, or vice versa), which reads as more unnatural than either choice alone.
- **Emotional delivery has exactly one real lever: the text handed to Bulbul.** Reconfirmed against Sarvam's current docs (fresh fetch, not memory) that Bulbul v3 exposes NO emotion/style/persona/SSML/pause/emphasis parameter of any kind — the model is explicitly built on an LLM that infers pauses, emphasis, and tone *from the text and punctuation itself* ("analyze text and infer the prosodic elements of natural speech"). So the only way to change how it sounds is to change what it's asked to say, which is done in two places:
  - **The system prompt** now tells Gemini (which has full conversational context, so it can judge *where* a pause belongs far better than any rule-based rewrite) to write with `"..."`/`"—"` pause punctuation at emotional beats (e.g. "मुझे यह सुनकर दुख हुआ।" → "ओह... मुझे यह सुनकर सचमुच बहुत अफ़सोस हुआ।"), a concrete rotating opener pool (ओह.../अच्छा.../ठीक है.../सबसे पहले.../मैं समझ सकता हूँ...), and — newly wired up — an instruction to read and act on `tone_reminder`, a field that was *already* being sent in every tool response (inherited from `DispatcherSession._tone_reminder()` in `dispatcher_live.py`, computed from casualties/Trapped/Heavy bleeding/Conscious/Breathing state) but that the Hindi prompt never told the model to actually use. This is the "emotion should scale with severity" mechanism — distressing turns get real, paused concern; routine turns stay calm and professional — and it was sitting there unused, not something new that had to be built.
  - **`_render_for_speech()`** — a genuine code-level speech-rendering layer between Gemini's output and Bulbul (Gemini's raw reply is never sent to Bulbul directly), called from `_agent_turn` right before `_speak_or_fallback`. Deliberately narrow and deterministic (no extra LLM call — that would double Gemini latency and API cost for a project that's already latency-sensitive and credit-constrained): it only (1) normalizes a known opener followed by a flat comma into the pause punctuation actually asked for, and (2) mechanically strips an opener if it exactly repeats the previous turn's, which prompting alone can't 100%-guarantee since it depends on the model remembering. Every entry in `_OPENERS` is a complete, self-contained clause, so stripping one is always grammatically safe. Unit-tested with plain Python (no API calls) in project history — if extending this function, keep doing that; it doesn't need a live Sarvam/Vertex round-trip to verify string-transform logic.
  - What this does **not** do: rewrite Gemini's actual word choice or semantic content — that remains entirely the reasoning model's job, informed by the prompt above. Nothing here can be verified by ear (no one in this loop can listen to the output); confidence rests on Sarvam's own documented claim that punctuation drives its prosody inference, not on a subjective "sounds more human" check.
- **Pronunciation**: raw digit strings and mixed-script codes read badly through TTS. The opening line's "1033" was being read as a cardinal number ("one thousand thirty-three") because it was left as the literal digits `1033` — fixed by hardcoding `_HINDI_OPENING_LINE` (a Hindi-only constant in `dispatcher_hindi.py`, deliberately NOT importing `dispatcher_live.py`'s shared `_OPENING_LINE["hi-IN"]`, to keep English's copy completely untouched) with `1033` spelled digit-by-digit as "एक शून्य तीन तीन" — how phone/helpline numbers are actually read aloud in any language. The system prompt also now tells the model to phonetically spell out any other numeric/mixed-script code it needs to say (e.g. a highway route number like "NH-27") rather than embedding raw Latin text mid-Hindi-sentence, since a Hindi TTS engine handles code-switched text unreliably. If pronunciation issues persist, get the *exact* mispronounced word/phrase from the user before guessing further — "a few words wrong" without examples is otherwise unfalsifiable.

## API Key Architecture (two keys, never mix them)

| Key | Env var | Reaches browser? | Restrictions |
|-----|---------|-----------------|--------------|
| Browser key | `NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY` | Yes (Maps JS bundle) | HTTP referrers + Maps JavaScript API only |
| Server key | `GOOGLE_MAPS_SERVER_KEY` | **Never** | IP addresses + Routes API, Places API (New), Geocoding API |

- Server key is used **only** inside `/api/routes/*` and `/api/places/*` route handlers.
- Never prefix the server key with `NEXT_PUBLIC_`.
- `.env.local` is gitignored. `.env.example` (committed) has placeholder values only.
- Keys go in `.env` or `.env.local` — both are read by Next.js on startup, restart required after changes.

**Map ID:** Uses `DEMO_MAP_ID` (Google's official testing ID that enables `AdvancedMarker` without Cloud Console setup).

---

## Project File Map

```
src/
  app/
    page.tsx                   — root page, renders <MapLoader />
    layout.tsx                 — HTML shell, Tailwind, metadata
    api/
      assess/route.ts          — POST: Anthropic severity assessment
      severity/route.ts        — (legacy alias for assess)
      places/nearby/route.ts   — GET: Google Places Nearby Search (New API)
      routes/matrix/route.ts   — POST: Routes API computeRouteMatrix (NDJSON)
      routes/single/route.ts   — POST: Routes API computeRoutes + polyline decode

  components/
    MapLoader.tsx              — dynamic import of MapView with ssr:false
    MapView.tsx                — main map component (all markers, tabs, overlays)
    LanguageToggle.tsx         — EN/HI/AS switcher in header
    TimelinePanel.tsx          — slide-in event log panel
    IncidentRecord.tsx         — full incident record overlay (printable)
    report/
      ReportPanel.tsx          — three-mode incident report (SOS / Text / Voice)
      MatchingPanel.tsx        — hospital + police matching with traffic ETAs

  hooks/
    useI18n.ts                 — useT() hook, reads from useLocaleStore
    usePlaces.ts               — fetches all Google Places layers, LAYER_TO_PLACE_TYPE map
    useVoiceInput.ts           — Web Speech API hook

  i18n/
    strings.ts                 — flat string map for all UI text (EN/HI/AS)

  lib/
    types.ts                   — all TypeScript interfaces (AccidentReport, HospitalCandidate,
                                  RankedHospital, DispatchRecord, etc.)
    candidates.ts              — hybrid hospital candidate set: curated + Google Places,
                                  dedup (≤500m or name token overlap), ranking by traffic + capability
    matching.ts                — rankHospitals(), rankPolice(), generateReasoning()
    dispatch.ts                — buildDispatchRecord(), formatSMSAlert()
    heuristic.ts               — rule-based severity scoring (fallback when AI key missing)
    incidentRecord.ts          — builds the full IncidentRecord text/object for the overlay
    geocode.ts                 — Nominatim reverse geocode
    osrm.ts                    — legacy OSRM code (kept but unused; Routes API replaced it)
    dedup.ts                   — duplicate incident detection (500m / 10 min window)

  store/
    eventLog.ts                — Zustand append-only event log
    routingStore.ts            — Zustand map polyline store
    localeStore.ts             — Zustand locale store (sessionStorage-persisted)

data/                          — all seed JSON, every record has "sample": true
  hospitals.json               — curated hospitals with trauma level + specialties
  police-stations.json         — curated police stations
  ambulance-stations.json      — 8 synthetic 108-network posts (Guwahati area)
  suraksha-mitras.json         — 5 synthetic highway patrol posts
  blackspots.json              — 9 synthetic accident blackspots
  potholes.json                — 12 synthetic road defects
  ambulances.json              — (legacy seed, superseded by ambulance-stations.json)
  mechanics.json               — (legacy seed, superseded by Google Places)
```

---

## MapView — Layers & Markers

### Service tab (Google Places — live)
| Layer key | Color | Marker shape | Icon |
|-----------|-------|-------------|------|
| `HOSPITAL` | Blue `#2563eb` | Rounded square | Medical cross |
| `POLICE` | Dark navy `#1e3a8a` | Rounded square | Shield + checkmark |
| `MECHANIC` | Gray `#6b7280` | Rounded square | Gear/cog |
| `PHARMACY` | Purple `#7c3aed` | Rounded square | Pill capsule |
| `GAS_STATION` | Cyan `#0891b2` | Rounded square | Fuel pump |

### Service tab (synthetic — sample data)
| Layer key | Color | Marker shape | Icon |
|-----------|-------|-------------|------|
| `AMBULANCE_STATION` | Green `#16a34a` | Circle | Star of life ⊕ |
| `SURAKSHA_MITRA` | Amber `#d97706` | Circle | Person silhouette |

### Accidents tab (synthetic — sample data)
| Layer key | Color | Marker shape | Icon |
|-----------|-------|-------------|------|
| `BLACKSPOT` | Red `#dc2626` | Triangle (warning sign) | Exclamation mark |
| `POTHOLE` | Brown `#78350f` | Diamond (road hazard marker) | Road with hole |

**Incident pin:** Amber teardrop with alert symbol + `animate-ping` pulse ring.

**Filter chips:** Each chip shows a mini version of the marker's shape (not just a circle), so the legend matches the map.

---

## Marker coordinate rules (Brahmaputra river)

The south bank of the Brahmaputra through Guwahati is roughly:
- lng 91.70 → bank ≈ 26.167N
- lng 91.74 → bank ≈ 26.183N
- lng 91.76 → bank ≈ 26.187N
- lng 91.80 → bank ≈ 26.172N

Any synthetic marker with `lat > bank_at_that_lng` is in the river. All current markers have been verified on land. When adding new seed data, run the bank check:
```python
# rough formula — see data correction session for full interpolation table
if lat > bank_lat(lng):
    print("IN RIVER — fix coordinates")
```

---

## Incident Report Flow (ReportPanel)

Three modes — all produce the same `AccidentReport` object:

1. **SOS** — uses browser Geolocation API, description auto-filled as "SOS — details unknown"
2. **Text** — manual: tap map to pin location → fill description → tick conditions → submit
3. **Voice** — Web Speech API transcribes speech into the description field (editable before submit)

**Real-time incident classification hint** (as user types description):
- Keyword-matches description + selected flags
- Shows a colour-coded card (appears below the textarea, disappears when text cleared):
  - 🔴 Injury crash → "Hospital + ambulance dispatch prioritised · police alerted"
  - 🔴 Medical emergency → "Routing to nearest hospital · ambulance dispatch initiated"
  - 🔵 Road collision → "Police dispatch + hospital on standby"
  - 🟠 Vehicle breakdown → "Mechanic stations highlighted on map · tow assistance flagged"
  - 🟠 Fire/fuel hazard → "Emergency response units alerted · hospital on standby"
  - 🟡 Road hazard → "Traffic police + road authority notified"
- Also reacts to Quick Flags: "Heavy bleeding" or "Trapped" upgrades any hint to medical tier

**After submit:**
1. Duplicate check (`dedup.ts`) — 500m / 10-min window, user can proceed or skip
2. Severity assessment → POST `/api/assess` → `claude-sonnet-4-6`; falls back to `heuristicAssess()` on 401/network error (shown as amber "Heuristic fallback" card — this is by design)
3. Hospital + police matching (`MatchingPanel.tsx`) — 3-phase async:
   - Phase 1: fetch `/api/places/nearby?type=hospital` (Google Places)
   - Phase 2: POST `/api/routes/matrix` — one batch Route Matrix call for nearest 10 hospitals (TRAFFIC_AWARE)
   - Phase 3: parallel `/api/routes/single` for #1 hospital + nearest police (polyline + distance)
4. Dispatch alert preview → confirm → record logged
5. Incident Record overlay (printable)

---

## Hospital Matching Logic (`candidates.ts` + `MatchingPanel.tsx`)

1. **Candidate set:** curated hospitals (`data/hospitals.json`) + Google Places hospitals near incident (radius 30 km)
2. **Dedup:** same facility if within 500m OR name token overlap (≥2 matching words >3 chars, excluding "hospital", "medical", "centre", "center", "district")
3. **Curated** keeps `traumaLevel`, `specialty[]`, `capabilitySource: "curated"`
4. **Google-only** gets `traumaLevel: null`, `capabilitySource: "unverified"`, shows "⚠ Unverified" pill in UI
5. **Shortlist:** sort ALL candidates by haversine distance → keep nearest 10
6. **Route Matrix:** one batch call → `TRAFFIC_AWARE` drive times
7. **Rank:** score = `1000 - durationMin + capabilityBonus + specialtyMatches×30`
   - `capabilityBonus`: −50 unverified, +200/+100/+50 for L1/L2/L3 trauma at severity ≥ 4
8. **Display:** top 3 with live drive times; beds greyed "Awaiting capacity feed" (never fabricated)
9. **Route polyline:** drawn for #1 hospital + nearest police via `computeRoutes`

---

## ETA Label — exact wording (never deviate)

```
Est. drive time from [Facility Name], current traffic — vehicle leaving now
```

This describes a hypothetical drive time if a vehicle left the facility now. It does NOT imply dispatch, tracking, or arrival.

Route legend in MatchingPanel:
```
Est. drive time from facility, current traffic — vehicle leaving now.
We do not track ambulances or police vehicles.
```

---

## i18n

- All user-visible strings are in `src/i18n/strings.ts` as a flat `Record<StringKey, Record<"EN"|"HI"|"AS", string>>`
- `useT()` hook returns a `t(key)` function bound to the current locale
- Locale stored in `useLocaleStore` (Zustand), persisted to `sessionStorage`
- Voice input locale follows UI locale (`en-IN`, `hi-IN`, `as-IN`)

---

## Zustand Stores

| Store | File | What it holds |
|-------|------|---------------|
| `useEventLog` | `store/eventLog.ts` | Append-only array of `EventEntry` objects — timestamped system events shown in TimelinePanel |
| `useRoutingStore` | `store/routingStore.ts` | Array of route polylines `{id, color, dashArray?, coords, label}` drawn on the map |
| `useLocaleStore` | `store/localeStore.ts` | Current locale string, persisted to `sessionStorage` |

Event log entry types: `incident_received`, `duplicate_check`, `assessment_complete`, `hospital_matched`, `route_estimated`, `dispatch_sent`

---

## What still needs real data to go live

| Gap | Current state | What's needed |
|-----|---------------|---------------|
| 108 ambulance stations | 8 synthetic posts | GVK EMRI / NHM Assam live roster |
| Suraksha Mitra roster | 5 synthetic patrollers | Assam Transport Dept current deployment list |
| Accident hotspots | 9 synthetic blackspots | iRAD / Assam Police FIR aggregates |
| Potholes | 12 synthetic defects | Assam PWD portal or crowdsourced feed |
| Hospital beds | "Awaiting capacity feed" shown | Hospital FHIR endpoint or NHM aggregator |
| Dispatch delivery | Message text logged only | SMS gateway (BSNL/TRAI sender ID) + delivery callback |
| Authentication | None | Role-based access (operator / supervisor / read-only) |
| Persistent state | Zustand in-memory | PostgreSQL append-only events table |

---

## Environment Variables

```bash
# Required for AI severity assessment (falls back to heuristic if missing/invalid)
ANTHROPIC_API_KEY=sk-ant-api03-...

# Required for map tiles (app won't render map without this)
NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY=AIza...

# Required for Places + Routes (app degrades gracefully: straight-line ranking, no polylines)
GOOGLE_MAPS_SERVER_KEY=AIza...
```

Put these in `.env` or `.env.local` — both work in Next.js. Restart the dev server after any change. Never prefix the server key with `NEXT_PUBLIC_`.

**Python voice-dispatcher backend (`app.py` — Railway, not Vercel):** set these on the Railway service (or repo-root `.env` for local `uvicorn app:app`), never on Vercel — the browser never talks to Sarvam or Vertex directly, only to this backend's WebSocket.

```bash
# Hindi dispatcher only (severity_engine/dispatcher_hindi.py + sarvam_speech.py)
SARVAM_API_KEY=              # dashboard.sarvam.ai
SARVAM_STT_MODEL=saaras-v3
SARVAM_TTS_MODEL=bulbul-v3
SARVAM_TTS_SPEAKER=shubh     # male voice, per user preference -- keep dispatcher_hindi.py's grammar (पुल्लिंग/स्त्रीलिंग) in sync with whichever gender is set here
SARVAM_TTS_PACE=1.1          # tuned by iterative user feedback (1.0 -> 1.15 -> 1.3 -> 1.2 -> 1.1); pace confirmed a real, functioning param via earlier live testing -- don't burn API credits re-verifying it
SARVAM_TTS_TEMPERATURE=0.7   # real, documented Bulbul v3 param (no pitch/loudness/SSML support)
SARVAM_TTS_MIN_BUFFER_CHARS=50   # lower = faster time-to-first-audio-chunk, less prosody smoothing
SARVAM_TTS_MAX_CHUNK_CHARS=150
SARVAM_STT_INTERRUPT_MIN_FRAMES=   # optional; raise only if echo (no headphones) false-triggers barge-in
HINDI_LATENCY_LOG=true       # per-turn [latency] breakdown in server logs
GEMINI_TEXT_MODEL=gemini-2.5-flash   # plain generate_content, NOT Gemini Live; gemini-2.0-flash 404s on this Vertex project/region; flash-lite was tried and rejected (empty responses + a role-validation 400 in testing)

# English dispatcher only (severity_engine/dispatcher_live.py) — unaffected by the above
GEMINI_LIVE_MODEL=gemini-live-2.5-flash-native-audio
VERTEX_AI_LOCATION=us-central1

# Shared Vertex AI / Speech-to-Text credentials (English Live + Hindi text-Gemini + Chirp STT)
GOOGLE_SERVICE_ACCOUNT_JSON_BASE64=   # or GOOGLE_SERVICE_ACCOUNT_JSON, or a local file for dev — see google_credentials.py
```

---

## Degraded Mode (missing keys)

| Missing key | Degradation |
|-------------|-------------|
| `NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY` | Blank map area, all other features work |
| `GOOGLE_MAPS_SERVER_KEY` | Places markers hidden; hospital ranking uses straight-line distance; no polylines; amber notice shown |
| `ANTHROPIC_API_KEY` (or invalid) | Severity uses rule-based heuristic; amber "Heuristic fallback" card shown — this is normal and by design |
