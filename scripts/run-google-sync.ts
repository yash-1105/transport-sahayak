// One-off local runner for the Google Places → Aggregator ingestion (no Vercel
// timeout). Reads SIGNALS_API_URL / SIGNALS_REGISTRY_API_KEY / GOOGLE_MAPS_SERVER_KEY
// from the environment and runs the SAME runGoogleSync the responders route auto-
// triggers. Usage: `set -a; . ./.env.local; set +a; npx tsx scripts/run-google-sync.ts`
import { runGoogleSync } from "../src/lib/aggregator/googleSync";

runGoogleSync()
  .then((s) => {
    console.log("GOOGLE SYNC DONE:", JSON.stringify(s));
    process.exit(0);
  })
  .catch((e) => {
    console.error("GOOGLE SYNC FAILED:", e);
    process.exit(1);
  });
