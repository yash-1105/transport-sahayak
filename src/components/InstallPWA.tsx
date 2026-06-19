"use client";

import { useEffect, useState } from "react";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export default function InstallPWA() {
  const [deferredPrompt, setDeferredPrompt] =
    useState<BeforeInstallPromptEvent | null>(null);
  const [isIOS, setIsIOS] = useState(false);
  const [showIOSHint, setShowIOSHint] = useState(false);
  const [installed, setInstalled] = useState(false);

  useEffect(() => {
    // Register service worker
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }

    // Already installed as standalone PWA — hide the button
    if (window.matchMedia("(display-mode: standalone)").matches) {
      setInstalled(true);
      return;
    }

    const ios =
      /iphone|ipad|ipod/i.test(navigator.userAgent) &&
      !(window.navigator as { standalone?: boolean }).standalone;
    setIsIOS(ios);

    const handler = (e: Event) => {
      e.preventDefault();
      setDeferredPrompt(e as BeforeInstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", handler);
    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, []);

  if (installed) return null;

  // On Android/Chrome: show install button when prompt is available
  if (deferredPrompt) {
    return (
      <button
        onClick={async () => {
          await deferredPrompt.prompt();
          const { outcome } = await deferredPrompt.userChoice;
          if (outcome === "accepted") setInstalled(true);
          setDeferredPrompt(null);
        }}
        className="flex items-center gap-1.5 rounded-full bg-red-600 px-3 py-1.5 text-xs font-semibold text-white shadow-lg active:scale-95 transition-transform md:hidden"
        title="Install app"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 20 20"
          fill="currentColor"
          className="h-4 w-4 shrink-0"
        >
          <path
            fillRule="evenodd"
            d="M10 3a1 1 0 011 1v7.586l2.293-2.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L9 11.586V4a1 1 0 011-1z"
            clipRule="evenodd"
          />
          <path d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" />
        </svg>
        Install app
      </button>
    );
  }

  // On iOS Safari: show hint with share icon instructions
  if (isIOS) {
    return (
      <>
        <button
          onClick={() => setShowIOSHint((v) => !v)}
          className="flex items-center gap-1.5 rounded-full bg-red-600 px-3 py-1.5 text-xs font-semibold text-white shadow-lg active:scale-95 transition-transform md:hidden"
          title="Install app"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            className="h-4 w-4 shrink-0"
          >
            <path
              fillRule="evenodd"
              d="M10 3a1 1 0 011 1v7.586l2.293-2.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L9 11.586V4a1 1 0 011-1z"
              clipRule="evenodd"
            />
            <path d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" />
          </svg>
          Install app
        </button>

        {showIOSHint && (
          <div className="fixed bottom-4 left-4 right-4 z-50 rounded-xl bg-white p-4 shadow-2xl border border-gray-100 md:hidden">
            <button
              onClick={() => setShowIOSHint(false)}
              className="absolute top-3 right-3 text-gray-400 hover:text-gray-600"
            >
              ✕
            </button>
            <p className="text-sm font-semibold text-gray-800 mb-2">
              Install Transport Sahayak
            </p>
            <ol className="text-sm text-gray-600 space-y-1.5 list-decimal list-inside">
              <li>
                Tap the{" "}
                <span className="inline-flex items-center gap-0.5 font-medium text-blue-600">
                  Share{" "}
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4 inline">
                    <path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8M16 6l-4-4-4 4M12 2v13" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </span>{" "}
                button in Safari
              </li>
              <li>
                Scroll down and tap{" "}
                <span className="font-medium text-gray-800">
                  &ldquo;Add to Home Screen&rdquo;
                </span>
              </li>
              <li>Tap &ldquo;Add&rdquo; to confirm</li>
            </ol>
          </div>
        )}
      </>
    );
  }

  return null;
}
