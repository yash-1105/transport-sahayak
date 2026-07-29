import type { Metadata } from "next";
import { IBM_Plex_Sans, Noto_Sans_Devanagari } from "next/font/google";
import "./globals.css";

// Design system fonts (UI redesign): IBM Plex Sans for Latin text, Noto Sans
// Devanagari for the bilingual Hindi labels shown throughout. Both are wired
// into the body font stack via CSS variables in globals.css.
const plexSans = IBM_Plex_Sans({
  variable: "--font-plex-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const notoDevanagari = Noto_Sans_Devanagari({
  variable: "--font-noto-devanagari",
  subsets: ["devanagari"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Transport Sahayak — Delhi–Dehradun Expressway Corridor",
  description: "Road Accident First Response — Proof of Concept",
  manifest: "/manifest.json",
  icons: {
    apple: "/icon-192.png",
    icon: [
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Sahayak",
  },
  other: {
    "mobile-web-app-capable": "yes",
    "msapplication-TileColor": "#dc2626",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${plexSans.variable} ${notoDevanagari.variable} h-full antialiased`}
    >
      <body className="h-full overflow-hidden">
        {children}
      </body>
    </html>
  );
}
