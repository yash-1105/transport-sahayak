"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { GeoPoint } from "@/lib/types";
import { reverseGeocode } from "@/lib/geocode";
import type {
  DispatchBriefingServices,
  DispatcherSubmitPayload,
  HelplineFacility,
} from "@/hooks/useVoiceDispatcher";

// ── AI TEXT-CHAT dispatcher (English only) ────────────────────────────────────
// A typed chatbot version of the voice dispatcher: the user types to an AI
// operator instead of calling. It talks to the SAME backend machinery as voice
// (the shared DispatcherSession tool handlers / next_question sequencing / submit
// gating / transcript backstop) over a DIFFERENT WebSocket (/ws/chat, backed by
// severity_engine/dispatcher_chat.TextChatSession, reasoning on Sarvam) — with
// NO audio at all. It deliberately does NOT import from or touch
// useVoiceDispatcher's audio pipeline (mic / AudioWorklet / playback); it shares
// only the plain TS types. The browser-facing protocol is the voice protocol
// minus every audio frame: {user_text} in; {ready|status|assistant_text|
// form_update|request_location|submitted|call_complete|error} out.
//
// It exposes the SAME callback contract subset ReportPanel already wires for the
// voice dispatcher (onDescription / onVehiclesInvolved / onCasualties / onSetFlag
// / onSubType / onLocationCaptured / onSubmitReady / getManualLocation) plus
// sendDispatchBriefing, so the EXISTING assess → MatchingPanel → dispatch_update
// flow is reused unchanged. English only.

export type ChatStatus =
  | "idle"
  | "connecting"
  | "thinking"
  | "waiting"      // waiting for the user's next message
  | "submitted"    // report submitted; matching + closing briefing in progress
  | "complete"     // closing briefing delivered, chat ended
  | "error"
  | "offline";     // chat backend not configured (NEXT_PUBLIC_*_WS_URL unset)

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  at: number;
}

export interface UseTextChatCallbacks {
  onDescription: (v: string) => void;
  onVehiclesInvolved: (v: number) => void;
  onCasualties: (v: number) => void;
  onSetFlag: (flag: string, active: boolean) => void;
  onSubType: (subType: string, category: string) => void;
  onLocationCaptured: (loc: GeoPoint, label: string) => void;
  onSubmitReady: (payload: DispatcherSubmitPayload) => void;
  /** A manually-set incident location (map pin), or null. Used for the
   * dispatcher's location instead of device GPS when present. */
  getManualLocation?: () => { lat: number; lng: number; label: string } | null;
  /** find_nearest_facility: the backend asks the frontend to compute the nearest
   * facility of a type from responder data + the set location, and return the
   * match (name / distance / estimated drive-time) or null. Real data only. */
  onFacilityQuery?: (req: {
    facilityType: string;
    capability: string | null;
    location: { lat: number; lng: number; label?: string } | null;
  }) => HelplineFacility | null | Promise<HelplineFacility | null>;
}

export interface UseTextChat {
  supported: boolean;
  status: ChatStatus;
  error: string | null;
  offline: boolean;
  messages: ChatMessage[];
  start: () => void;
  stop: () => void;
  send: (text: string) => void;
  /** After submission, hand the backend the responder ETAs the dashboard is
   * displaying so it can deliver the closing briefing as a chat message. No-op
   * if the chat already ended. */
  sendDispatchBriefing: (services: DispatchBriefingServices) => void;
  /** Link this chat's committed INC-… id (kept for parity with the voice hook;
   * chat does not post call metrics). */
  attachIncidentId: (id: string) => void;
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
  facilityType?: string;
  capability?: string | null;
  location?: { lat: number; lng: number; label?: string } | null;
}

function getChatWsUrl(): string | null {
  // Same backend host as the voice dispatcher — an explicit override wins, else
  // derive /ws/chat from the dispatcher URL (/ws/dispatcher). No locale param:
  // the chat is English-only.
  const explicit = process.env.NEXT_PUBLIC_CHAT_WS_URL;
  if (explicit) return explicit;
  const base = process.env.NEXT_PUBLIC_DISPATCHER_WS_URL;
  if (!base) return null;
  const noQuery = base.split("?")[0];
  return noQuery.replace(/\/ws\/dispatcher$/, "/ws/chat");
}

function chatSupported(): boolean {
  return typeof window !== "undefined" && typeof WebSocket !== "undefined";
}

export function useTextChat(callbacks: UseTextChatCallbacks): UseTextChat {
  const cbRef = useRef(callbacks);
  cbRef.current = callbacks;

  const [status, setStatus] = useState<ChatStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const incidentIdRef = useRef<string | null>(null);
  const endedRef = useRef(false);
  const startedRef = useRef(false);

  const supported = chatSupported();
  const offline = supported && getChatWsUrl() === null;

  const appendMessage = useCallback((role: "user" | "assistant", text: string) => {
    setMessages((prev) => [...prev, { role, text, at: Date.now() }]);
  }, []);

  const teardown = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null;
      try {
        ws.close();
      } catch {
        /* already closing */
      }
    }
  }, []);

  const handleServerEvent = useCallback((event: ServerEvent) => {
    const cb = cbRef.current;
    switch (event.type) {
      case "ready":
        break; // backend follows this with its opening reply (status + assistant_text)
      case "status":
        if (event.state === "thinking") setStatus("thinking");
        else if (event.state === "listening") setStatus((s) => (s === "submitted" ? s : "waiting"));
        break;
      case "assistant_text":
        if (typeof event.text === "string" && event.text.trim()) {
          appendMessage("assistant", event.text);
          setStatus((s) => (s === "submitted" || s === "complete" ? s : "waiting"));
        }
        break;
      case "transcript":
        break; // the assistant reply is delivered via assistant_text; ignore the mirror
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
        const ws = wsRef.current;
        // Prefer a manually-set map pin over device GPS (same rule as voice).
        const manual = cb.getManualLocation?.();
        if (manual) {
          cb.onLocationCaptured({ lat: manual.lat, lng: manual.lng }, manual.label);
          ws?.send(JSON.stringify({ type: "location_result", requestId, lat: manual.lat, lng: manual.lng, label: manual.label }));
          break;
        }
        if (typeof navigator === "undefined" || !navigator.geolocation) {
          ws?.send(JSON.stringify({ type: "location_error", requestId, message: "Geolocation not supported" }));
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
      case "request_facility": {
        // find_nearest_facility: compute the nearest facility from responder
        // data (via onFacilityQuery) and reply. The backend awaits this (it
        // times out gracefully if we can't answer).
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
          wsRef.current?.send(JSON.stringify({
            type: "facility_result",
            requestId,
            facility,
            needsLocation: !facility && !loc && !cb.getManualLocation?.(),
          }));
        })();
        break;
      }
      case "submitted": {
        setStatus("submitted");
        if (event.incident) cb.onSubmitReady(event.incident);
        break;
      }
      case "call_complete":
        setStatus("complete");
        endedRef.current = true;
        break;
      case "error":
        setError(event.message || "The chat dispatcher hit a problem.");
        setStatus("error");
        break;
      default:
        break;
    }
  }, [appendMessage]);

  const start = useCallback(() => {
    if (!supported || startedRef.current) return;
    const url = getChatWsUrl();
    if (!url) {
      setStatus("offline");
      return;
    }
    startedRef.current = true;
    endedRef.current = false;
    setError(null);
    setMessages([]);
    setStatus("connecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      setStatus("error");
      setError("Could not connect to the chat dispatcher.");
      startedRef.current = false;
      return;
    }
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        handleServerEvent(JSON.parse(ev.data as string) as ServerEvent);
      } catch {
        /* ignore non-JSON frames */
      }
    };
    ws.onerror = () => {
      console.error("[chat] websocket error");
    };
    ws.onclose = () => {
      // A close after call_complete is a normal end; otherwise, if we never
      // completed, surface it (unless the user stopped intentionally).
      if (!endedRef.current && startedRef.current) {
        setStatus((s) => (s === "complete" ? s : s === "error" ? s : "error"));
      }
    };
  }, [supported, handleServerEvent]);

  const stop = useCallback(() => {
    startedRef.current = false;
    endedRef.current = true;
    teardown();
    setStatus("idle");
  }, [teardown]);

  const send = useCallback((text: string) => {
    const t = text.trim();
    const ws = wsRef.current;
    if (!t || !ws || ws.readyState !== WebSocket.OPEN) return;
    appendMessage("user", t);
    setStatus("thinking");
    ws.send(JSON.stringify({ type: "user_text", text: t }));
  }, [appendMessage]);

  const sendDispatchBriefing = useCallback((services: DispatchBriefingServices) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "dispatch_update", services }));
  }, []);

  const attachIncidentId = useCallback((id: string) => {
    incidentIdRef.current = id;
  }, []);

  // Close the socket on unmount.
  useEffect(() => () => { startedRef.current = false; teardown(); }, [teardown]);

  return {
    supported,
    status,
    error,
    offline,
    messages,
    start,
    stop,
    send,
    sendDispatchBriefing,
    attachIncidentId,
  };
}
