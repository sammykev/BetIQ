import { NextRequest, NextResponse } from "next/server";

// Page view → backend traffic counts (backend/traffic.py). Adds what only
// Vercel knows: the visitor's country, region, city and rough coordinates
// (from its x-vercel-ip-* headers), plus device/browser/OS from the user
// agent. The visitor's IP is never forwarded or stored. Bots are dropped.

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";
// Link-preview fetchers and crawlers; WhatsApp's preview UA starts with "WhatsApp/"
// (its in-app browser doesn't), Telegram's contains "bot"
const BOTS = /bot|crawl|spider|slurp|facebookexternalhit|^whatsapp\/|headless|lighthouse|pingdom|uptime/i;

function device(ua: string): "mobile" | "tablet" | "desktop" {
  if (/ipad|tablet|(android(?!.*mobile))/i.test(ua)) return "tablet";
  if (/mobi|iphone|android/i.test(ua)) return "mobile";
  return "desktop";
}

function browser(ua: string): string {
  if (/opr\/|opera/i.test(ua)) return "Opera";
  if (/edg\//i.test(ua)) return "Edge";
  if (/samsungbrowser/i.test(ua)) return "Samsung";
  if (/ucbrowser/i.test(ua)) return "UC";
  if (/firefox|fxios/i.test(ua)) return "Firefox";
  if (/chrome|crios/i.test(ua)) return "Chrome";
  if (/safari/i.test(ua)) return "Safari";
  return "Other";
}

function os(ua: string): string {
  if (/android/i.test(ua)) return "Android";
  if (/iphone|ipad|ipod/i.test(ua)) return "iOS";
  if (/windows/i.test(ua)) return "Windows";
  if (/mac os/i.test(ua)) return "macOS";
  if (/linux/i.test(ua)) return "Linux";
  return "Other";
}

export async function POST(req: NextRequest) {
  const ua = req.headers.get("user-agent") || "";
  const key = process.env.TRAFFIC_KEY || process.env.ADMIN_SECRET;
  if (!key || !ua || BOTS.test(ua)) return new NextResponse(null, { status: 204 });
  let body: Record<string, unknown>;
  try {
    body = await req.json();
    if (!body || typeof body !== "object" || JSON.stringify(body).length > 2000) throw new Error();
  } catch {
    return new NextResponse(null, { status: 400 });
  }
  const h = (name: string) => {
    const v = req.headers.get(name);
    try { return v ? decodeURIComponent(v) : undefined; } catch { return v ?? undefined; }
  };
  const hit = {
    ...body,
    country: h("x-vercel-ip-country"), region: h("x-vercel-ip-country-region"), city: h("x-vercel-ip-city"),
    lat: h("x-vercel-ip-latitude"), lon: h("x-vercel-ip-longitude"),
    device: device(ua), browser: browser(ua), os: os(ua),
  };
  try {
    await fetch(`${API}/api/traffic/hit`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Traffic-Key": key },
      body: JSON.stringify(hit),
      signal: AbortSignal.timeout(4000),
    });
  } catch { /* a missed page view is fine */ }
  return new NextResponse(null, { status: 204 });
}
