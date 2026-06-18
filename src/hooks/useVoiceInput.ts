import { useState, useRef, useCallback } from "react";

export type VoiceLocale = "en-IN" | "hi-IN" | "as-IN";

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

// Web Speech API is not in TypeScript's standard DOM types; use any for the recognition instance.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyRec = any;

function getSpeechRecognition(): (new () => AnyRec) | null {
  if (typeof window === "undefined") return null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

const ERROR_MSGS: Record<string, string> = {
  "not-allowed": "Microphone access denied. Check browser permissions and try again.",
  "no-speech": "No speech detected. Speak closer to the microphone and try again.",
  "network": "Network error — browser may require internet for speech recognition.",
  "audio-capture": "Microphone not found or unavailable on this device.",
};

export function useVoiceInput(): UseVoiceInput {
  // Safe here — this hook is only called inside a dynamic-imported (ssr:false) client component.
  const [supported] = useState(() => getSpeechRecognition() !== null);
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [interimTranscript, setInterimTranscript] = useState("");
  const [error, setError] = useState<string | null>(null);
  const recRef = useRef<AnyRec>(null);

  const start = useCallback(
    (locale: VoiceLocale) => {
      const SR = getSpeechRecognition();
      if (!SR) return;
      setError(null);
      setInterimTranscript("");

      const rec = new SR();
      rec.lang = locale;
      rec.continuous = true;
      rec.interimResults = true;
      rec.maxAlternatives = 1;

      rec.onstart = () => setListening(true);

      rec.onend = () => {
        setListening(false);
        setInterimTranscript("");
      };

      rec.onerror = (e: AnyRec) => {
        setListening(false);
        if (e.error !== "aborted") {
          setError(ERROR_MSGS[e.error] ?? `Speech recognition error: ${e.error}`);
        }
      };

      rec.onresult = (e: AnyRec) => {
        let fin = "";
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const text: string = e.results[i][0].transcript;
          if (e.results[i].isFinal) fin += text;
          else interim += text;
        }
        if (fin) setTranscript((prev) => (prev ? prev + " " + fin.trim() : fin.trim()));
        setInterimTranscript(interim);
      };

      recRef.current = rec;
      rec.start();
    },
    [] // no deps needed — getSpeechRecognition reads window at call time
  );

  const stop = useCallback(() => {
    recRef.current?.stop();
    recRef.current = null;
  }, []);

  const clearTranscript = useCallback(() => {
    setTranscript("");
    setInterimTranscript("");
    setError(null);
  }, []);

  return { supported, listening, transcript, interimTranscript, error, start, stop, clearTranscript };
}
