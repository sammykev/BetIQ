// Set in .env.local (see .env.example); EXPO_PUBLIC_ values are built into the app.
export const API_URL = (process.env.EXPO_PUBLIC_API_URL || "https://84-12-80-126.sslip.io").replace(/\/$/, "");
export const SITE_URL = (process.env.EXPO_PUBLIC_SITE_URL || "https://predict-withbetiq.vercel.app").replace(/\/$/, "");
export const CLERK_PUBLISHABLE_KEY = process.env.EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY || "";
