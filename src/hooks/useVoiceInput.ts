import { useState, useRef, useCallback } from "react";

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

// Web Speech API is not in TypeScript's standard DOM types; use any for the recognition instance.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyRec = any;

function getSpeechRecognition(): (new () => AnyRec) | null {
  if (typeof window === "undefined") return null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

const DOMAIN_WORDS: Record<VoiceLocale, string[]> = {
  "en-IN": [
    "accident", "crash", "collision", "vehicle", "truck", "bus", "motorcycle",
    "car", "injury", "injured", "trapped", "bleeding", "unconscious",
    "ambulance", "police", "hospital", "road", "highway", "overturned",
    "casualties", "fire", "fuel", "pothole", "SOS", "emergency",
  ],
  "hi-IN": [
    "दुर्घटना", "वाहन", "घायल", "फँसा", "खून", "अस्पताल", "पुलिस",
    "एम्बुलेंस", "ट्रक", "बस", "मोटरसाइकिल", "सड़क", "राजमार्ग",
    "आग", "ईंधन", "टक्कर", "बेहोश", "आपातकाल",
  ],
};

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
      rec.maxAlternatives = 3;

      // Bias recognition toward accident-domain vocabulary (Chrome/Edge only; silently ignored elsewhere)
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const GL = (window as any).SpeechGrammarList ?? (window as any).webkitSpeechGrammarList;
        if (GL) {
          const words = DOMAIN_WORDS[locale] ?? DOMAIN_WORDS["en-IN"];
          const grammar = `#JSGF V1.0; grammar terms; public <term> = ${words.join(" | ")};`;
          const list = new GL();
          list.addFromString(grammar, 1);
          rec.grammars = list;
        }
      } catch { /* unsupported browser — no-op */ }

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
          const result = e.results[i];
          // Pick the alternative with highest confidence among up to maxAlternatives
          let bestText: string = result[0].transcript;
          let bestConf: number = result[0].confidence ?? 0;
          for (let j = 1; j < result.length; j++) {
            const conf: number = result[j].confidence ?? 0;
            if (conf > bestConf) { bestConf = conf; bestText = result[j].transcript; }
          }
          if (result.isFinal) fin += bestText;
          else interim += bestText;
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
