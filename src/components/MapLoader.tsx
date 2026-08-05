"use client";

import dynamic from "next/dynamic";
import AuthGate from "@/components/auth/AuthGate";

const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center bg-gray-100">
      <p className="text-sm text-gray-500">Loading map…</p>
    </div>
  ),
});

// The full-screen auth gate wraps MapView, so the map (and its responder /
// route fetches) only mount once the user is signed in or continues as a guest.
export default function MapLoader() {
  return (
    <AuthGate>
      <MapView />
    </AuthGate>
  );
}
