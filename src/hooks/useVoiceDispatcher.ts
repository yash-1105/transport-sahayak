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

export interface UseVoiceDispatcherCallbacks {
  onDescription: (v: string) => void;
  onVehiclesInvolved: (v: number) => void;
  onCasualties: (v: number) => void;
  onSetFlag: (flag: string, active: boolean) => void;
  onSubType: (subType: string, category: string) => void;
  onLocationCaptured: (loc: GeoPoint, label: string) => void;
  onSubmitReady: (payload: DispatcherSubmitPayload) => void;
}

export interface UseVoiceDispatcher {
  supported: boolean;
  status: DispatcherStatus;
  error: string | null;
  offline: boolean;
  start: (locale: VoiceLocale) => void;
  stop: () => void;
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
const PLAYBACK_SAMPLE_RATE = 24000; // Gemini Live's fixed output rate, confirmed via live testing
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
}

export function useVoiceDispatcher(callbacks: UseVoiceDispatcherCallbacks): UseVoiceDispatcher {
  const [supported] = useState(isSupported);
  const [status, setStatus] = useState<DispatcherStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const localeRef = useRef<VoiceLocale>("en-IN");
  const reconnectAttemptRef = useRef(0);
  const intentionalStopRef = useRef(false);
  const sessionIdRef = useRef(0);

  // Playback scheduling state
  const playbackCtxRef = useRef<AudioContext | null>(null);
  const nextStartTimeRef = useRef(0);
  const activeSourcesRef = useRef<AudioBufferSourceNode[]>([]);

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

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime, nextStartTimeRef.current);
    source.start(startAt);
    nextStartTimeRef.current = startAt + buffer.duration;
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
    teardown();
    setStatus("ended");
  }, [teardown]);

  const handleServerEvent = useCallback(
    (event: ServerEvent, isStale: () => boolean) => {
      if (isStale()) return;
      const cb = callbacksRef.current;
      switch (event.type) {
        case "ready":
          reconnectAttemptRef.current = 0;
          setStatus("listening");
          break;
        case "status":
          if (event.state === "listening" || event.state === "thinking" || event.state === "speaking" || event.state === "reconnecting") {
            setStatus(event.state);
          }
          break;
        case "turn_complete":
          setStatus("listening");
          break;
        case "interrupted":
          flushPlayback();
          break;
        case "form_update": {
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
        case "submitted":
          if (event.incident) cb.onSubmitReady(event.incident);
          break;
        case "error":
          setError(event.message ?? "Voice dispatcher error.");
          setStatus("error");
          break;
        case "transcript":
          // Internal only — never rendered, per spec (no STT UI in this tab).
          break;
        default:
          break;
      }
    },
    [flushPlayback]
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
      const ws = new WebSocket(wsUrl);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
        if (isStale()) return;
        if (event.data instanceof ArrayBuffer) {
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

      ws.onclose = () => {
        if (wsRef.current === ws) wsRef.current = null;
        if (isStale() || intentionalStopRef.current) return;
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
        void startCapture(sessionId, isStale, ws);
      };
    },
    [playChunk, handleServerEvent]
  );

  const startCapture = useCallback(
    async (sessionId: number, isStale: () => boolean, ws: WebSocket) => {
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
          if (ws.readyState === WebSocket.OPEN) {
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

  const start = useCallback(
    (locale: VoiceLocale) => {
      if (!supported) {
        setError(ERROR_MSGS["audio-capture"]);
        setStatus("error");
        return;
      }
      intentionalStopRef.current = false;
      reconnectAttemptRef.current = 0;
      localeRef.current = locale;
      const sessionId = ++sessionIdRef.current;
      setError(null);
      connect(locale, sessionId, false);
    },
    [supported, connect]
  );

  useEffect(() => () => teardown(), [teardown]);

  return { supported, status, error, offline, start, stop };
}
