import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

// Sent with every page and API response the middleware runs on. No full
// Content-Security-Policy: Clerk and Paystack load their own scripts, so
// only framing is restricted (stops clickjacking).
const SECURITY_HEADERS: Record<string, string> = {
  "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "SAMEORIGIN",
  "Content-Security-Policy": "frame-ancestors 'self'",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
};

export default clerkMiddleware(() => {
  const res = NextResponse.next();
  for (const [k, v] of Object.entries(SECURITY_HEADERS)) res.headers.set(k, v);
  return res;
});

export const config = {
  // Only run Clerk on page routes and specific API routes — never on /api/og
  matcher: [
    "/((?!_next|api/og|opengraph-image|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|webmanifest)).*)",
    "/api/subscribe(.*)",
    "/api/chat(.*)",
  ],
};
