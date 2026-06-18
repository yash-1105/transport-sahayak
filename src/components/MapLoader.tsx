"use client";

import dynamic from "next/dynamic";

const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => (
    <div className="flex h-screen items-center justify-center bg-gray-100">
      <p className="text-sm text-gray-500">Loading map…</p>
    </div>
  ),
});

export default function MapLoader() {
  return <MapView />;
}
