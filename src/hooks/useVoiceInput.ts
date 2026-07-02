import { useCallback, useRef, useState } from "react";

// Server-side streaming speech recognition (Google Cloud Speech-to-Text V2 /
// Chirp), replacing the previous browser SpeechRecognition/webkitSpeechRecognition
// implementation. Public contract is unchanged — every field/method below has
// the exact same name, type, and behavior as before, so ReportPanel.tsx's
// VoiceSection needs zero changes.
//
// Audio pipeline: mic -> AudioWorklet (public/audio/pcm16-worklet.js, converts
// to mono PCM16 @ 16kHz) -> WebSocket (binary frames) -> backend /ws/voice
// (severity_engine/voice_stream.py) -> Speech-to-Text V2 StreamingRecognize.
// The backend sends back JSON text frames: {"type":"interim"|"final","text":
// string} or {"type":"error","message":string}.

export type VoiceLocale = "en-IN" | "hi-IN";

export interface UseVoiceInput {
  supported: boolean;
  listening: boolean;
  transcript: string;
  interimTranscript: string;
  error: string | null;
  start: (locale: VoiceLocale) => void;
  stop: () => void;
  clearTranscript: () => void;
}

const ERROR_MSGS: Record<string, string> = {
  "not-allowed": "Microphone access denied. Check browser permissions and try again.",
  "audio-capture": "Microphone not found or unavailable on this device.",
  "network": "Network error — check your connection and try again.",
  "not-configured": "Voice recognition is not configured for this deployment.",
};

const WORKLET_URL = "/audio/pcm16-worklet.js";
const WORKLET_NAME = "pcm16-processor";
const TARGET_SAMPLE_RATE = 16000;

interface TranscriptEvent {
  type: "interim" | "final" | "error";
  text?: string;
  message?: string;
}

function getVoiceStreamUrl(): string | null {
  return process.env.NEXT_PUBLIC_VOICE_STREAM_URL || null;
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

export function useVoiceInput(): UseVoiceInput {
  // Safe here — this hook is only called inside a dynamic-imported (ssr:false) client component.
  const [supported] = useState(isSupported);
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [interimTranscript, setInterimTranscript] = useState("");
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  // Guards against a stale async start() (e.g. two rapid taps) tearing down
  // a newer session's resources, or writing state after stop() already ran.
  const sessionIdRef = useRef(0);

  const teardown = useCallback(() => {
    workletNodeRef.current?.port.close();
    workletNodeRef.current?.disconnect();
    workletNodeRef.current = null;

    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;

    if (audioContextRef.current && audioContextRef.current.state !== "closed") {
      audioContextRef.current.close().catch(() => {
        /* already closing/closed — nothing to do */
      });
    }
    audioContextRef.current = null;

    if (wsRef.current) {
      const ws = wsRef.current;
      wsRef.current = null;
      // Avoid firing onclose's state updates for a socket we're deliberately closing.
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
    }
  }, []);

  const stop = useCallback(() => {
    sessionIdRef.current += 1; // invalidate any in-flight start()
    teardown();
    setListening(false);
    setInterimTranscript("");
  }, [teardown]);

  const start = useCallback(
    (_locale: VoiceLocale) => {
      // Both supported languages (English + Hindi) are always recognized
      // together server-side (see severity_engine/voice_stream.py's
      // _LANGUAGE_CODES) — the locale argument is kept for interface
      // compatibility with the previous browser-API-backed hook, which used
      // it to pick a single recognition language.
      if (!supported) {
        setError(ERROR_MSGS["audio-capture"]);
        return;
      }
      const wsUrl = getVoiceStreamUrl();
      if (!wsUrl) {
        setError(ERROR_MSGS["not-configured"]);
        return;
      }

      const sessionId = ++sessionIdRef.current;
      const isStale = () => sessionId !== sessionIdRef.current;

      setError(null);
      setInterimTranscript("");

      void (async () => {
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
          return;
        }
        if (isStale()) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;

        const ws = new WebSocket(wsUrl);
        ws.binaryType = "arraybuffer";
        wsRef.current = ws;

        ws.onmessage = (event: MessageEvent<string>) => {
          if (isStale()) return;
          let msg: TranscriptEvent;
          try {
            msg = JSON.parse(event.data) as TranscriptEvent;
          } catch {
            return; // ignore malformed frames
          }
          if (msg.type === "final" && msg.text) {
            const finalText = msg.text;
            setTranscript((prev) => (prev ? prev + " " + finalText.trim() : finalText.trim()));
            setInterimTranscript("");
          } else if (msg.type === "interim") {
            setInterimTranscript(msg.text ?? "");
          } else if (msg.type === "error") {
            setError(msg.message ?? "Speech recognition error.");
          }
        };

        ws.onerror = () => {
          if (isStale()) return;
          setError(ERROR_MSGS["network"]);
        };

        ws.onclose = () => {
          if (isStale()) return;
          setListening(false);
          setInterimTranscript("");
        };

        ws.onopen = () => {
          void (async () => {
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
              // we only want to capture audio, never play the mic back out.
              source.connect(workletNode);

              setListening(true);
            } catch {
              if (isStale()) return;
              setError("Could not start audio capture in this browser.");
              stop();
            }
          })();
        };
      })();
    },
    [supported, stop]
  );

  const clearTranscript = useCallback(() => {
    setTranscript("");
    setInterimTranscript("");
    setError(null);
  }, []);

  return { supported, listening, transcript, interimTranscript, error, start, stop, clearTranscript };
}
