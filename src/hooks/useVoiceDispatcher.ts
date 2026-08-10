import { useCallback, useEffect, useRef, useState } from "react";
import type { GeoPoint } from "@/lib/types";
import { reverseGeocode } from "@/lib/geocode";
import type { VoiceLocale } from "@/hooks/useVoiceInput";

// Conversational voice dispatcher (Gemini Live via Vertex AI) — a separate,
// new integration from useVoiceInput.ts (Chirp speech-to-text), which this
// hook does not touch, import from, or share state with. Audio capture
// reuses the same AudioWorklet-based PCM16/16kHz mic pipeline (copy, not a
// shared import, to keep this hook fully decoupled) talking to a different
// backend WebSocket (/ws/dispatcher, see severity_engine/dispatcher_live.py)
// that runs a full function-calling conversation instead of plain transcription.
//
// New territory this hook adds beyond useVoiceInput.ts: bidirectional audio.
// The server streams back PCM16/24kHz synthesized speech, played via queued
// AudioBufferSourceNodes (Web Audio auto-resamples 24kHz -> the browser's
// native output rate, no manual resampler needed).

export type DispatcherStatus =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "briefing"
  | "reconnecting"
  | "error"
  | "ended";

export interface DispatcherFormUpdate {
  field: "incidentType" | "description" | "vehiclesInvolved" | "casualties" | "flag";
  value: unknown;
}

export interface DispatcherSubmitPayload {
  subType: string | null;
  category: string | null;
  description: string;
  vehiclesInvolved: number | null;
  casualties: number | null;
  flags: string[];
  location: { lat: number; lng: number; label: string } | null;
}

/** Phase 3: one nearest-facility result for the helpline `find_nearest_facility`
 * tool. `etaMinutes` is a labelled straight-line ESTIMATE, never a tracked time.
 * `note` optionally qualifies the match (e.g. trauma-capability). */
export interface HelplineFacility {
  name: string;
  contactNumber: string | null;
  distanceKm: number;
  etaMinutes: number;
  note?: string | null;
}

export interface UseVoiceDispatcherCallbacks {
  onDescription: (v: string) => void;
  onVehiclesInvolved: (v: number) => void;
  onCasualties: (v: number) => void;
  onSetFlag: (flag: string, active: boolean) => void;
  onSubType: (subType: string, category: string) => void;
  onLocationCaptured: (loc: GeoPoint, label: string) => void;
  onSubmitReady: (payload: DispatcherSubmitPayload) => void;
  /** #4 (Hindi staged dispatch): the backend notified one or more responders
   * early, mid-call, as interim notifications (never with an ETA). Fired with
   * the service keys ("ambulance" | "fire" | "towing") and the incident
   * location the backend used. ReportPanel matches the nearest responder(s),
   * logs an honest notification record, and shows a chip. English never sends
   * this (the English pipeline is untouched by #4). */
  onInterimDispatch?: (
    services: string[],
    location: { lat: number; lng: number; label?: string } | null
  ) => void;
  /** Phase 3 (multi-intent helpline, Hindi): the backend's `find_nearest_facility`
   * tool asks the frontend to find the nearest facility of a type from the
   * responder data it already has, plus a labelled drive-time ESTIMATE. Returns
   * the match (or null if none / no location). English never sends this. */
  onFacilityQuery?: (req: {
    facilityType: string;
    capability: string | null;
    location: { lat: number; lng: number; label?: string } | null;
  }) => HelplineFacility | null | Promise<HelplineFacility | null>;
  /** Phase 3: the backend's `lodge_complaint` tool asks the frontend to log a
   * road-defect/complaint record (reusing the pothole path) and return a REAL
   * reference id. Returns null on failure. English never sends this. */
  onLodgeComplaint?: (req: {
    description: string;
    complaintType: string;
    location: { lat: number; lng: number; label?: string } | null;
  }) => string | null | Promise<string | null>;
  /** A manually-set incident location (map pin), or null if none is set. When
   * present it is used for the dispatcher's location instead of device GPS, so
   * a user who dropped a pin before starting isn't overridden by geolocation. */
  getManualLocation?: () => { lat: number; lng: number; label: string } | null;
}

/** One responder entry for the post-submission voice briefing — the SAME
 * values the dashboard is already displaying (sourced from the event log's
 * ROUTE_ESTIMATED / HOSPITAL_MATCHED entries), never recomputed. */
export interface DispatchBriefingService {
  name: string;
  etaMinutes: number | null;
  distanceKm: number | null;
}

export interface DispatchBriefingServices {
  ambulance?: DispatchBriefingService;
  fire?: DispatchBriefingService;
  towing?: DispatchBriefingService;
  hospital?: DispatchBriefingService;
  police?: DispatchBriefingService;
}

export interface UseVoiceDispatcher {
  supported: boolean;
  status: DispatcherStatus;
  error: string | null;
  offline: boolean;
  /** Fallback only: the agent's reply as text when speech synthesis failed
   * server-side (Hindi/Sarvam path) — null whenever audio is working. */
  agentText: string | null;
  start: (locale: VoiceLocale) => void;
  stop: () => void;
  /** After submission, hand the backend the responder ETAs the dashboard is
   * displaying so the agent can announce them and close the call. No-op if
   * the call already ended. */
  sendDispatchBriefing: (services: DispatchBriefingServices) => void;
  /** Link this call's captured metrics to the committed INC-… id. Called by
   * ReportPanel once it creates the incident in the dispatcher submit path. */
  attachIncidentId: (id: string) => void;
}

// ── Post-Call Analytics capture (client-side) ─────────────────────────────────
// Accumulated across the call lifecycle from the WS events the hook already
// receives. HONESTY (Hard Rules): the time-to-dispatch clock stops at the
// `submitted` event — nothing here ever times the post-submit briefing / SOPs /
// ETAs. POSTed fire-and-forget to /api/call-metrics on call end (every call,
// any caller; reads are operator-gated in the UI).
interface CallMetricsState {
  posted: boolean;
  incidentId: string | null;
  locale: VoiceLocale;
  startedAt: number | null; // ms epoch — start() pressed
  readyAt: number | null; // ms epoch — `ready` (call connected = clock start)
  dispatchedAt: number | null; // ms epoch — `submitted` (info collection done)
  // #4: ms epoch of the FIRST real responder dispatch — the interim ambulance
  // dispatch (Hindi staged flow), which can precede `submitted`. time-to-dispatch
  // is measured to THIS when present, else to `dispatchedAt` (submit) as before.
  firstDispatchAt: number | null;
  callerTurns: number;
  agentTurns: number;
  questionsAsked: number; // agent turns DURING info collection (before dispatch)
  productiveTurns: number; // agent turns whose caller exchange yielded a form_update
  fields: { field: string; at_ms: number }[];
  transcript: { role: string; at_ms: number; text: string }[];
  reconnects: number;
  pendingProductive: boolean; // a form_update landed since the last turn_complete
  // The call's classified intent (from the backend `call_intent` frame). A
  // Hindi "information" call (facility/scheme/complaint/breakdown) is logged with
  // outcome "information" so it is NOT counted as an abandoned accident call.
  // null = accident call (English always) or a call that ended before any intent.
  callType: "accident" | "information" | null;
}

function freshMetrics(locale: VoiceLocale): CallMetricsState {
  return {
    posted: false, incidentId: null, locale,
    startedAt: Date.now(), readyAt: null, dispatchedAt: null, firstDispatchAt: null,
    callerTurns: 0, agentTurns: 0, questionsAsked: 0, productiveTurns: 0,
    fields: [], transcript: [], reconnects: 0, pendingProductive: false, callType: null,
  };
}

const ERROR_MSGS: Record<string, string> = {
  "not-allowed": "Microphone access denied. Check browser permissions and try again.",
  "audio-capture": "Microphone not found or unavailable on this device.",
  "network": "Network error — check your connection and try again.",
  "not-configured": "The voice dispatcher is not configured for this deployment.",
};

const WORKLET_URL = "/audio/pcm16-worklet.js";
const WORKLET_NAME = "pcm16-processor";
const TARGET_SAMPLE_RATE = 16000;
const PLAYBACK_SAMPLE_RATE = 24000; // Gemini Live's fixed output rate, confirmed via live testing; Bulbul v3 (Hindi) is configured server-side to the same rate
// Gemini Live's native-audio model has no API-level speaking-rate control, so
// this is enforced client-side. A mild slowdown for clarity — low enough that
// the pitch-lowering side effect of simple rate-based playback stays natural.
// Hindi (Sarvam Bulbul TTS) already speaks at a natural operator pace and has
// a server-side pace control, so it plays at 1.0 — the slowdown is a
// Gemini-Live-specific compensation, not a general preference.
const PLAYBACK_RATE = 0.88;
const PLAYBACK_RATE_HINDI = 1.0;
const END_SIGNAL = JSON.stringify({ type: "end" });
const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_DELAYS_MS = [1000, 2000, 4000];

function getDispatcherWsUrl(locale: VoiceLocale): string | null {
  const base = process.env.NEXT_PUBLIC_DISPATCHER_WS_URL;
  if (!base) return null;
  const separator = base.includes("?") ? "&" : "?";
  return `${base}${separator}locale=${encodeURIComponent(locale)}`;
}

function isSupported(): boolean {
  if (typeof window === "undefined") return false;
  return (
    !!navigator.mediaDevices?.getUserMedia &&
    typeof AudioContext !== "undefined" &&
    typeof AudioWorkletNode !== "undefined" &&
    typeof WebSocket !== "undefined"
  );
}

interface ServerEvent {
  type: string;
  field?: string;
  value?: unknown;
  state?: string;
  requestId?: string;
  incident?: DispatcherSubmitPayload;
  message?: string;
  role?: string;
  text?: string;
  services?: unknown;
  location?: { lat: number; lng: number; label?: string } | null;
  facilityType?: string;
  capability?: string | null;
  description?: string;
  complaintType?: string;
  intent?: string;
}

export function useVoiceDispatcher(callbacks: UseVoiceDispatcherCallbacks): UseVoiceDispatcher {
  const [supported] = useState(isSupported);
  const [status, setStatus] = useState<DispatcherStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [agentText, setAgentText] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const localeRef = useRef<VoiceLocale>("en-IN");
  const reconnectAttemptRef = useRef(0);
  const intentionalStopRef = useRef(false);
  const sessionIdRef = useRef(0);
  // Mirrors `status` for the worklet's onmessage closure, which is created
  // once in startCapture() and can't see fresh React state without this.
  const statusRef = useRef<DispatcherStatus>("idle");
  // Set once submit_incident has fired. The call no longer ends here — the
  // backend keeps the session open to deliver the closing briefing (responder
  // ETAs + safety guidance) and sends "call_complete" when it's truly over.
  // This ref makes a post-submission socket close read as a normal call end
  // (old backend, or backend finished and closed) instead of a reconnectable
  // network error.
  const submittedRef = useRef(false);
  // Incremented on every "status" event of any kind — lets a delayed
  // "listening" transition (see the "status" handler) detect that a newer
  // status has since superseded it and skip applying a now-stale mic-open.
  const statusSeqRef = useRef(0);
  // Logs "Audio playback started" exactly once per call, the first binary
  // chunk received while status is "briefing" (the Gemini-Flash-script +
  // Google-Cloud-TTS audio, as opposed to Gemini Live's own conversational
  // audio) — actual playback only ever happens client-side, so this is
  // logged here rather than trusted from a backend claim (see the backend's
  // own send-side "Sending TTS audio" log, which is a true statement about
  // what it sent, not about what the browser has played).
  const briefingPlaybackLoggedRef = useRef(false);

  // Playback scheduling state
  const playbackCtxRef = useRef<AudioContext | null>(null);
  const nextStartTimeRef = useRef(0);
  const activeSourcesRef = useRef<AudioBufferSourceNode[]>([]);

  // Post-Call Analytics accumulator (see CallMetricsState). Null until start().
  const metricsRef = useRef<CallMetricsState | null>(null);

  // Relative timeline (ms) for the per-turn drill-down: measured from call
  // connect (`ready`), falling back to start().
  const atMs = useCallback((): number => {
    const m = metricsRef.current;
    if (!m) return 0;
    return Date.now() - (m.readyAt ?? m.startedAt ?? Date.now());
  }, []);

  // Finalize + POST the call's metrics exactly once, on the first end signal
  // (call_complete = dispatched; stop/unmount = abandoned/error). Only calls
  // that actually CONNECTED (readyAt set) are recorded — a call that never
  // connected is an infra failure, not a dispatcher-performance data point.
  const finalizeMetrics = useCallback((forcedOutcome?: "dispatched" | "abandoned" | "error") => {
    const m = metricsRef.current;
    if (!m || m.posted || m.readyAt == null) return;
    m.posted = true;
    const endedAt = Date.now();
    // #4: the actual dispatch moment is the FIRST responder dispatch — the
    // interim ambulance dispatch when the call staged one (Hindi), otherwise the
    // `submitted` event as before. A call that dispatched an ambulance early
    // then dropped still counts as "dispatched".
    const dispatchMoment = m.firstDispatchAt ?? m.dispatchedAt;
    // A general "information" call is its own outcome — never "abandoned" — so it
    // doesn't drag down the accident completion rate. Accident calls (dispatched /
    // abandoned / error) are unchanged. Priority: a real dispatch wins (an
    // accident that both asked a question and dispatched), then information.
    const outcome =
      forcedOutcome ??
      (dispatchMoment
        ? "dispatched"
        : m.callType === "information"
          ? "information"
          : statusRef.current === "error"
            ? "error"
            : "abandoned");
    const body = {
      incident_id: m.incidentId,
      locale: m.locale,
      outcome,
      started_at: m.startedAt ? new Date(m.startedAt).toISOString() : null,
      ready_at: m.readyAt ? new Date(m.readyAt).toISOString() : null,
      dispatched_at: dispatchMoment ? new Date(dispatchMoment).toISOString() : null,
      ended_at: new Date(endedAt).toISOString(),
      // CORE metric — clock stops at the actual dispatch moment (interim
      // ambulance dispatch when staged, else `submitted`); never times the briefing.
      time_to_dispatch_ms: dispatchMoment && m.readyAt ? dispatchMoment - m.readyAt : null,
      call_duration_ms: m.startedAt ? endedAt - m.startedAt : null,
      caller_turns: m.callerTurns,
      agent_turns: m.agentTurns,
      total_turns: m.callerTurns + m.agentTurns,
      questions_asked: m.questionsAsked,
      productive_turns: m.productiveTurns,
      fields_collected: m.fields,
      reconnects: m.reconnects,
      transcript: m.transcript,
    };
    // Fire-and-forget — analytics must never affect the call UX.
    void fetch("/api/call-metrics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true, // let it complete even if the page is unloading
    }).catch(() => { /* best-effort */ });
  }, []);

  const attachIncidentId = useCallback((id: string) => {
    if (metricsRef.current) metricsRef.current.incidentId = id;
  }, []);

  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  useEffect(() => {
    const goOffline = () => setOffline(true);
    const goOnline = () => setOffline(false);
    window.addEventListener("offline", goOffline);
    window.addEventListener("online", goOnline);
    return () => {
      window.removeEventListener("offline", goOffline);
      window.removeEventListener("online", goOnline);
    };
  }, []);

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  // How many seconds of already-scheduled audio are still queued on the Web
  // Audio timeline (see playChunk's nextStartTimeRef). Shared by every place
  // that needs to wait out queued playback before tearing down -- see the
  // "call_complete" and ws.onclose handlers below, which both need it for
  // the same reason: once audio chunks are ALREADY scheduled via playChunk,
  // that scheduled playback is entirely client-side (Web Audio) and keeps
  // running to completion regardless of the WebSocket's state, so neither
  // handler should stop it early just because a message arrived or the
  // socket closed.
  const remainingPlaybackSeconds = useCallback(() => {
    const ctx = playbackCtxRef.current;
    return ctx && ctx.state !== "closed"
      ? Math.max(0, nextStartTimeRef.current - ctx.currentTime)
      : 0;
  }, []);

  const flushPlayback = useCallback(() => {
    activeSourcesRef.current.splice(0).forEach((src) => {
      try {
        src.stop();
      } catch {
        /* already stopped */
      }
    });
    if (playbackCtxRef.current) {
      nextStartTimeRef.current = playbackCtxRef.current.currentTime;
    }
  }, []);

  const playChunk = useCallback((data: ArrayBuffer) => {
    let ctx = playbackCtxRef.current;
    if (!ctx || ctx.state === "closed") {
      ctx = new AudioContext();
      playbackCtxRef.current = ctx;
      nextStartTimeRef.current = ctx.currentTime;
    }
    const int16 = new Int16Array(data);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;

    const buffer = ctx.createBuffer(1, float32.length, PLAYBACK_SAMPLE_RATE);
    buffer.copyToChannel(float32, 0);

    // hi-IN (Bulbul) and as-IN (ElevenLabs eleven_v3) both stream native-rate
    // 24 kHz PCM, so they play at 1.0; the 0.88 slowdown is a Gemini-Live-only
    // (en-IN) compensation.
    const playbackRate =
      localeRef.current === "hi-IN" || localeRef.current === "as-IN"
        ? PLAYBACK_RATE_HINDI
        : PLAYBACK_RATE;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.playbackRate.value = playbackRate;
    source.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime, nextStartTimeRef.current);
    source.start(startAt);
    // Slower playbackRate stretches actual duration beyond buffer.duration —
    // must schedule off the real playback time or chunks start overlapping.
    nextStartTimeRef.current = startAt + buffer.duration / playbackRate;
    activeSourcesRef.current.push(source);
    source.onended = () => {
      activeSourcesRef.current = activeSourcesRef.current.filter((s) => s !== source);
    };
  }, []);

  const stopCapture = useCallback(() => {
    workletNodeRef.current?.port.close();
    workletNodeRef.current?.disconnect();
    workletNodeRef.current = null;

    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;

    if (audioContextRef.current && audioContextRef.current.state !== "closed") {
      audioContextRef.current.close().catch(() => {
        /* already closing/closed */
      });
    }
    audioContextRef.current = null;
  }, []);

  const teardown = useCallback(() => {
    stopCapture();
    flushPlayback();
    if (playbackCtxRef.current && playbackCtxRef.current.state !== "closed") {
      playbackCtxRef.current.close().catch(() => {});
    }
    playbackCtxRef.current = null;
    if (wsRef.current) {
      const ws = wsRef.current;
      wsRef.current = null;
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
    }
  }, [stopCapture, flushPlayback]);

  const stop = useCallback(() => {
    intentionalStopRef.current = true;
    sessionIdRef.current += 1;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      try {
        wsRef.current.send(END_SIGNAL);
      } catch {
        /* socket already going away */
      }
    }
    // Metrics: derive outcome (dispatched if `submitted` fired, else abandoned /
    // error). Idempotent — a no-op if call_complete already finalized.
    finalizeMetrics();
    teardown();
    setStatus("ended");
  }, [teardown, finalizeMetrics]);

  const handleServerEvent = useCallback(
    (event: ServerEvent, isStale: () => boolean) => {
      if (isStale()) return;
      const cb = callbacksRef.current;
      switch (event.type) {
        case "ready":
          // "ready" only means the socket handshake succeeded -- it fires
          // BEFORE the backend's scripted opening greeting actually starts
          // generating audio (kicked off separately, right after this
          // message). Treating it as "listening" opened a window where the
          // mic could transmit real audio while the model's kickoff turn was
          // still being set up, which was confusing it into restarting/
          // repeating the opening line. Stay "connecting" (mic stays gated)
          // until a real "status":"listening" arrives, which only happens
          // once the greeting's turn has actually completed.
          reconnectAttemptRef.current = 0;
          // Metrics: call connected — this is the time-to-dispatch clock start.
          if (metricsRef.current && metricsRef.current.readyAt == null) {
            metricsRef.current.readyAt = Date.now();
          }
          setStatus("connecting");
          break;
        case "status": {
          if (
            event.state !== "listening" &&
            event.state !== "thinking" &&
            event.state !== "speaking" &&
            event.state !== "briefing" &&
            event.state !== "reconnecting"
          ) {
            break;
          }
          if (event.state === "briefing") {
            briefingPlaybackLoggedRef.current = false;
          }
          statusSeqRef.current += 1;
          const seq = statusSeqRef.current;
          if (event.state !== "listening") {
            setStatus(event.state);
            break;
          }
          // Do NOT open the mic the instant the server says "listening" --
          // that only means Gemini Live finished GENERATING this turn's
          // audio, not that the browser has finished PLAYING it (chunks are
          // queued ahead on the Web Audio timeline, see playChunk). Real
          // reported bug (no headphones): the mic reopened while the tail of
          // the agent's own opening line was still audibly playing through
          // the laptop speaker, got picked up by the mic, and Gemini Live --
          // reactive, so it responds to whatever it hears -- treated that
          // self-audio bleed as the caller describing an incident and
          // classified one without the caller ever having spoken. Wait for
          // whatever's still scheduled in the playback queue (plus a small
          // margin for the tail of the last chunk's natural decay) before
          // actually flipping to "listening", which is what gates the
          // worklet's micOpen check below. The seq guard drops this if a
          // newer status (e.g. a fresh "speaking" for the very next turn)
          // has since superseded it -- never force a stale mic-open.
          const ctx = playbackCtxRef.current;
          const remainingS = ctx && ctx.state !== "closed"
            ? Math.max(0, nextStartTimeRef.current - ctx.currentTime)
            : 0;
          setTimeout(() => {
            if (isStale() || statusSeqRef.current !== seq) return;
            setStatus("listening");
          }, remainingS * 1000 + 250);
          break;
        }
        case "call_complete": {
          // The closing briefing has been fully delivered server-side. Any
          // remaining audio may still be scheduled in the playback queue —
          // wait for it to drain before tearing down, so the goodbye is
          // never clipped.
          const remainingS = remainingPlaybackSeconds();
          const wasBriefing = statusRef.current === "briefing";
          // Metrics: the full call is over and it dispatched. Finalize now
          // (ended_at = now); the drain setTimeout below only handles audio.
          finalizeMetrics("dispatched");
          console.info(`[dispatcher] call_complete received — draining ${remainingS.toFixed(1)}s of queued audio, then ending`);
          setTimeout(() => {
            if (wasBriefing) {
              console.info("========================\nStage 12\nPlayback completed\n========================");
            }
            if (!isStale()) stop();
          }, remainingS * 1000 + 300);
          break;
        }
        case "interrupted":
          // Gemini Live's own conversational barge-in signal -- never
          // legitimately sent by the backend once the closing briefing
          // (Gemini Flash + Google TTS, entirely outside Gemini Live) has
          // started, since the code path that emits "interrupted" only
          // runs while the Gemini Live pump is still active, which stops
          // before the briefing phase begins. Defensive guard anyway: a
          // stray/stale one truncating the briefing via flushPlayback()
          // would look identical to the mid-briefing-cutoff bug this file
          // just fixed for "call_complete"/onclose, so never act on it
          // during the non-conversational "thinking"/"briefing" phases.
          if (statusRef.current !== "thinking" && statusRef.current !== "briefing") {
            flushPlayback();
          }
          break;
        case "form_update": {
          // Metrics: a new field was collected → this caller exchange advanced
          // data collection (productive). Record the field + its timeline mark.
          if (metricsRef.current && event.field) {
            metricsRef.current.fields.push({ field: event.field, at_ms: atMs() });
            metricsRef.current.pendingProductive = true;
          }
          if (event.field === "incidentType") {
            const v = event.value as { subType?: string; category?: string } | undefined;
            if (v?.subType && v?.category) cb.onSubType(v.subType, v.category);
          } else if (event.field === "description" && typeof event.value === "string") {
            cb.onDescription(event.value);
          } else if (event.field === "vehiclesInvolved" && typeof event.value === "number") {
            cb.onVehiclesInvolved(event.value);
          } else if (event.field === "casualties" && typeof event.value === "number") {
            cb.onCasualties(event.value);
          } else if (event.field === "flag") {
            const v = event.value as { flag_name?: string; flag_active?: boolean } | undefined;
            if (v?.flag_name) cb.onSetFlag(v.flag_name, !!v.flag_active);
          }
          break;
        }
        case "request_location": {
          const requestId = event.requestId;
          if (!requestId) break;
          // Prefer a MANUALLY-set location (map pin) over device GPS. If the
          // user dropped a pin before starting, use it and never call
          // geolocation (which would override their choice with current GPS).
          const manual = cb.getManualLocation?.();
          if (manual) {
            if (metricsRef.current) {
              metricsRef.current.fields.push({ field: "location", at_ms: atMs() });
              metricsRef.current.pendingProductive = true;
            }
            cb.onLocationCaptured({ lat: manual.lat, lng: manual.lng }, manual.label);
            wsRef.current?.send(JSON.stringify({ type: "location_result", requestId, lat: manual.lat, lng: manual.lng, label: manual.label }));
            break;
          }
          if (!navigator.geolocation) {
            wsRef.current?.send(JSON.stringify({ type: "location_error", requestId, message: "Geolocation not supported" }));
            break;
          }
          navigator.geolocation.getCurrentPosition(
            (pos) => {
              void (async () => {
                const lat = pos.coords.latitude;
                const lng = pos.coords.longitude;
                let label = "";
                try {
                  label = await reverseGeocode(lat, lng);
                } catch {
                  label = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
                }
                if (isStale()) return;
                // Metrics: location resolved counts as a collected field.
                if (metricsRef.current) {
                  metricsRef.current.fields.push({ field: "location", at_ms: atMs() });
                  metricsRef.current.pendingProductive = true;
                }
                cb.onLocationCaptured({ lat, lng }, label);
                wsRef.current?.send(JSON.stringify({ type: "location_result", requestId, lat, lng, label }));
              })();
            },
            (err) => {
              wsRef.current?.send(JSON.stringify({ type: "location_error", requestId, message: err.message }));
            },
            { enableHighAccuracy: true, timeout: 7000 }
          );
          break;
        }
        case "interim_dispatch": {
          // #4 (Hindi staged dispatch): the backend arranged one or more
          // responders early, mid-call. Hand the service list + location to
          // ReportPanel, which runs the nearest-responder match, logs the
          // notification record, and shows a chip. No ETA is ever implied here
          // (the real details come with the closing briefing).
          const services = Array.isArray(event.services)
            ? event.services.filter((s): s is string => typeof s === "string")
            : [];
          // Metrics: the ambulance dispatch is the actual "dispatch" moment for
          // time-to-dispatch (stamped once, and only if it precedes `submitted`).
          if (metricsRef.current && metricsRef.current.firstDispatchAt == null && services.includes("ambulance")) {
            metricsRef.current.firstDispatchAt = Date.now();
          }
          if (services.length) cb.onInterimDispatch?.(services, event.location ?? null);
          break;
        }
        case "request_facility": {
          // Phase 3 (helpline): find the nearest facility from responder data and
          // reply. Answered by ReportPanel via onFacilityQuery; the backend awaits
          // this reply (it times out gracefully if we can't answer).
          const requestId = event.requestId;
          if (!requestId) break;
          const loc = event.location ?? null;
          void (async () => {
            let facility: HelplineFacility | null = null;
            try {
              facility = (await cb.onFacilityQuery?.({
                facilityType: event.facilityType ?? "",
                capability: event.capability ?? null,
                location: loc,
              })) ?? null;
            } catch { /* reply null on any error */ }
            if (isStale()) return;
            wsRef.current?.send(JSON.stringify({
              type: "facility_result",
              requestId,
              facility,
              needsLocation: !facility && !loc && !cb.getManualLocation?.(),
            }));
          })();
          break;
        }
        case "call_intent": {
          // Classify the call for Post-Call Analytics. accident outranks
          // information and is never downgraded.
          const m = metricsRef.current;
          if (m && (event.intent === "accident" || event.intent === "information")) {
            if (m.callType !== "accident") m.callType = event.intent;
          }
          break;
        }
        case "request_complaint": {
          // Phase 3 (helpline): log a road-defect/complaint and return a real
          // reference id (ReportPanel reuses the pothole path).
          const requestId = event.requestId;
          if (!requestId) break;
          void (async () => {
            let referenceId: string | null = null;
            try {
              referenceId = (await cb.onLodgeComplaint?.({
                description: event.description ?? "",
                complaintType: event.complaintType ?? "other",
                location: event.location ?? null,
              })) ?? null;
            } catch { /* reply null on any error */ }
            if (isStale()) return;
            wsRef.current?.send(JSON.stringify({ type: "complaint_result", requestId, referenceId }));
          })();
          break;
        }
        case "submitted":
          // The call is NOT over yet: the backend now waits for the
          // dashboard's responder ETAs (sent back via sendDispatchBriefing)
          // and delivers the closing briefing, then sends "call_complete".
          // submittedRef only marks that a socket close from here on is a
          // normal call end, never a reconnectable drop (see ws.onclose).
          submittedRef.current = true;
          // Metrics: info collection is complete — STOP the time-to-dispatch
          // clock here. Nothing after this (briefing/SOPs/ETAs) is ever timed.
          if (metricsRef.current && metricsRef.current.dispatchedAt == null) {
            metricsRef.current.dispatchedAt = Date.now();
          }
          if (event.incident) cb.onSubmitReady(event.incident);
          break;
        case "error":
          console.error(`[dispatcher] backend error: ${event.message ?? "(no message)"}`);
          setError(event.message ?? "Voice dispatcher error.");
          setStatus("error");
          break;
        case "transcript": {
          // Internal only — never RENDERED in this tab (no STT UI). Captured for
          // Post-Call Analytics: attribute caller vs agent turns + keep the text
          // for the operator-only drill-down transcript.
          const m = metricsRef.current;
          if (m && typeof event.text === "string") {
            const role = event.role === "user" || event.role === "caller" ? "caller" : "agent";
            if (role === "caller") m.callerTurns += 1;
            m.transcript.push({ role, at_ms: atMs(), text: event.text });
          }
          break;
        }
        case "turn_complete": {
          // Metrics: one agent turn just finished. If the caller exchange it
          // belongs to produced a new form_update (pendingProductive), the turn
          // was productive (advanced collection); otherwise it was a re-ask /
          // clarification. questions_asked counts agent turns DURING info
          // collection (before dispatch).
          const m = metricsRef.current;
          if (m) {
            m.agentTurns += 1;
            if (m.dispatchedAt == null) m.questionsAsked += 1;
            if (m.pendingProductive) {
              m.productiveTurns += 1;
              m.pendingProductive = false;
            }
          }
          break;
        }
        case "tts_text":
          // Hindi/Sarvam path only: speech synthesis failed server-side, so
          // the agent's reply arrives as text to display instead of audio.
          if (typeof event.text === "string") setAgentText(event.text);
          break;
        default:
          break;
      }
    },
    [flushPlayback, stop, finalizeMetrics, atMs]
  );

  const startCapture = useCallback(
    async (sessionId: number, isStale: () => boolean) => {
      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        });
      } catch (err) {
        if (isStale()) return;
        const name = err instanceof DOMException ? err.name : "";
        setError(
          name === "NotAllowedError" || name === "PermissionDeniedError"
            ? ERROR_MSGS["not-allowed"]
            : name === "NotFoundError" || name === "DevicesNotFoundError"
              ? ERROR_MSGS["audio-capture"]
              : "Could not access the microphone. Check your device and try again."
        );
        setStatus("error");
        return;
      }
      if (isStale()) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      streamRef.current = stream;

      try {
        const audioContext = new AudioContext();
        if (isStale()) {
          audioContext.close().catch(() => {});
          return;
        }
        audioContextRef.current = audioContext;
        await audioContext.audioWorklet.addModule(WORKLET_URL);
        if (isStale()) return;

        const source = audioContext.createMediaStreamSource(stream);
        const workletNode = new AudioWorkletNode(audioContext, WORKLET_NAME, {
          processorOptions: { targetSampleRate: TARGET_SAMPLE_RATE },
        });
        workletNodeRef.current = workletNode;

        workletNode.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
          // Read wsRef.current fresh each call (not a captured `ws` param) so
          // this same worklet keeps working after a reconnect swaps in a new
          // socket, instead of silently sending into a closed one forever.
          // English (Gemini Live) only transmits while "listening" -- without
          // headphones the mic picks up its own voice, which the server was
          // misreading as the caller interrupting and repeating sentences
          // (see dispatcher_live.py's NO_INTERRUPTION config). Hindi (Sarvam)
          // and Assamese (Saaras STT) deliberately ALSO transmit while
          // "speaking": the backend needs live mic audio during agent playback
          // to detect a genuine caller barge-in and cut its own reply short (see
          // dispatcher_hindi.py's _speak_or_fallback, inherited by
          // dispatcher_assamese.py) -- this branch is unreachable for en-IN.
          const ws = wsRef.current;
          const micOpen =
            statusRef.current === "listening" ||
            ((localeRef.current === "hi-IN" || localeRef.current === "as-IN") &&
              statusRef.current === "speaking");
          if (ws?.readyState === WebSocket.OPEN && micOpen) {
            ws.send(e.data);
          }
        };

        // Deliberately not connecting workletNode to audioContext.destination —
        // only capturing the mic, never playing it back out.
        source.connect(workletNode);
      } catch {
        if (isStale()) return;
        setError("Could not start audio capture in this browser.");
        setStatus("error");
      }
    },
    []
  );

  const connect = useCallback(
    (locale: VoiceLocale, sessionId: number, isReconnect: boolean) => {
      const isStale = () => sessionId !== sessionIdRef.current;
      const wsUrl = getDispatcherWsUrl(locale);
      if (!wsUrl) {
        setError(ERROR_MSGS["not-configured"]);
        setStatus("error");
        return;
      }

      setStatus(isReconnect ? "reconnecting" : "connecting");
      if (isReconnect && metricsRef.current) metricsRef.current.reconnects += 1;
      const ws = new WebSocket(wsUrl);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
        if (isStale()) return;
        if (event.data instanceof ArrayBuffer) {
          if (statusRef.current === "briefing" && !briefingPlaybackLoggedRef.current) {
            briefingPlaybackLoggedRef.current = true;
            console.info(
              "========================\n" +
              "Stage 10\n" +
              `Frontend received audio (${event.data.byteLength} bytes, first chunk)\n` +
              "========================"
            );
            console.info("========================\nStage 11\nPlayback started\n========================");
          }
          playChunk(event.data);
          return;
        }
        let parsed: ServerEvent;
        try {
          parsed = JSON.parse(event.data) as ServerEvent;
        } catch {
          return;
        }
        handleServerEvent(parsed, isStale);
      };

      ws.onerror = () => {
        if (isStale()) return;
        setError(ERROR_MSGS["network"]);
      };

      ws.onclose = (ev) => {
        // Diagnostic: a close with submitted=true and no prior call_complete
        // means the backend hung up mid-briefing (see dispatcher_live.py's
        // reconnect-resume fix) — this log is what distinguishes that from a
        // normal post-briefing close in the browser console.
        console.info(`[dispatcher] websocket closed (code=${ev.code}, submitted=${submittedRef.current}, intentional=${intentionalStopRef.current})`);
        if (wsRef.current === ws) wsRef.current = null;
        if (isStale() || intentionalStopRef.current) return;
        if (submittedRef.current) {
          // The report went through — the backend closing the socket after
          // (or instead of) the closing briefing is a normal call end, not a
          // drop to reconnect from. Reconnecting here would start a brand new
          // session with a fresh opening greeting.
          //
          // Real reported bug: the agent started speaking the closing
          // briefing for real, then was "shut down abruptly" mid-sentence.
          // Root cause: this branch used to call stop() IMMEDIATELY, with
          // no regard for how much audio was still queued -- racing against,
          // and defeating, the "call_complete" handler's own correct drain
          // wait above. The backend closes its WebSocket very soon after
          // sending call_complete (see dispatcher_live.py), so onclose fires
          // within milliseconds -- while remainingPlaybackSeconds() can
          // still be several real seconds for a full ambulance/fire/towing/
          // SOP briefing. Whichever handler called stop() first won, and
          // onclose (near-instant) beat call_complete's setTimeout (seconds
          // long) essentially every time, truncating playback via stop()'s
          // flushPlayback(). Already-scheduled audio (playChunk's
          // AudioBufferSourceNodes) is entirely client-side Web Audio
          // playback and keeps running regardless of the WebSocket's state,
          // so there is no need to react to the socket closing at all until
          // that scheduled playback has actually finished -- drain the same
          // way call_complete already correctly does, instead of stopping
          // immediately.
          const remainingS = remainingPlaybackSeconds();
          setTimeout(() => {
            if (!isStale()) stop();
          }, remainingS * 1000 + 300);
          return;
        }
        if (reconnectAttemptRef.current < MAX_RECONNECT_ATTEMPTS) {
          const delay = RECONNECT_DELAYS_MS[reconnectAttemptRef.current] ?? 4000;
          reconnectAttemptRef.current += 1;
          setStatus("reconnecting");
          setTimeout(() => {
            if (!isStale() && !intentionalStopRef.current) connect(localeRef.current, sessionId, true);
          }, delay);
        } else {
          setStatus("error");
          setError(ERROR_MSGS["network"]);
        }
      };

      ws.onopen = () => {
        if (isReconnect) return; // mic capture is already running from the original start()
        void startCapture(sessionId, isStale);
      };
    },
    [playChunk, handleServerEvent, startCapture, stop]
  );

  const sendDispatchBriefing = useCallback((services: DispatchBriefingServices) => {
    const ws = wsRef.current;
    if (ws?.readyState !== WebSocket.OPEN) {
      // The one place this can silently no-op: if the WS closed (or never
      // opened) before the matching flow finished, dispatch_update is never
      // sent and the backend's _dispatch_ready.wait() times out after
      // DISPATCH_BRIEFING_WAIT_S (30s) instead of firing immediately — worth
      // knowing about explicitly rather than a bare silent return.
      console.warn(
        `[dispatcher] sendDispatchBriefing called but WS not OPEN (readyState=${ws?.readyState ?? "no socket"}) — dispatch_update NOT sent`
      );
      return;
    }
    try {
      ws.send(JSON.stringify({ type: "dispatch_update", services }));
      console.info("========================\nStage 3 (frontend)\nDispatch services calculated — sent to backend\n========================");
    } catch (e) {
      console.warn("[dispatcher] sendDispatchBriefing threw while sending:", e);
    }
  }, []);

  const start = useCallback(
    (locale: VoiceLocale) => {
      if (!supported) {
        setError(ERROR_MSGS["audio-capture"]);
        setStatus("error");
        return;
      }
      intentionalStopRef.current = false;
      submittedRef.current = false;
      reconnectAttemptRef.current = 0;
      localeRef.current = locale;
      metricsRef.current = freshMetrics(locale); // start a fresh metrics record
      const sessionId = ++sessionIdRef.current;
      setError(null);
      setAgentText(null);
      connect(locale, sessionId, false);
    },
    [supported, connect]
  );

  // On unmount (operator navigates away / panel closes mid-call): flush metrics
  // for an abandoned call, then tear down. The cleanup is held in a ref and the
  // teardown effect has EMPTY deps, so it fires ONLY on a real unmount -- never
  // when `teardown`/`finalizeMetrics` merely change identity. Real reported bug:
  // the SOS button remounts the panel (new key) and auto-starts a call in the
  // SAME commit; with these callbacks in the deps, a callback-identity change
  // during that render storm re-ran this effect and its cleanup tore down the
  // freshly-opened WebSocket mid-connect. sessionId had already advanced, so
  // onclose saw a stale session and never reconnected -- leaving the UI stuck on
  // "Connecting…". Manual start was fine because the panel was already stable.
  const unmountCleanupRef = useRef<() => void>(() => {});
  useEffect(() => {
    unmountCleanupRef.current = () => { finalizeMetrics(); teardown(); };
  }, [finalizeMetrics, teardown]);
  useEffect(() => () => { unmountCleanupRef.current(); }, []);

  return { supported, status, error, offline, agentText, start, stop, sendDispatchBriefing, attachIncidentId };
}
