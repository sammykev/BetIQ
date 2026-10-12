"use client";

import { useEffect } from "react";

// Sentry in the browser: errors on visitors' screens, with their stack traces,
// land in the Sentry project. Off (and not even downloaded) unless
// NEXT_PUBLIC_SENTRY_DSN is set in Vercel. No cookies, IP addresses, or
// query strings are sent.
const DSN = process.env.NEXT_PUBLIC_SENTRY_DSN;

export function ErrorTracking() {
  useEffect(() => {
    if (!DSN) return;
    import("@sentry/browser")
      .then((Sentry) => {
        Sentry.init({
          dsn: DSN,
          environment: process.env.NEXT_PUBLIC_VERCEL_ENV || "production",
          sendDefaultPii: false,
          // Errors from browser extensions and other sites' scripts aren't ours
          allowUrls: [window.location.origin],
          beforeSend(event) {
            if (event.request) {
              delete event.request.cookies;
              delete event.request.headers;
              delete event.request.query_string;
              if (event.request.url) event.request.url = event.request.url.split("?")[0];
            }
            delete event.user;
            return event;
          },
        });
      })
      .catch(() => {});
  }, []);
  return null;
}
