// Browser push: subscribe this browser to the daily odds notification.
import { API } from "@/lib/matchday";

export function pushSupported(): boolean {
  return typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

// The VAPID public key is URL-safe base64; pushManager.subscribe wants its bytes
function keyBytes(base64: string): Uint8Array<ArrayBuffer> {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function send(method: "POST" | "DELETE", sub: PushSubscription) {
  const res = await fetch(`${API}/api/push/subscribe`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subscription: sub.toJSON() }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || body.ok === false) throw new Error(body.error || `HTTP ${res.status}`);
}

export async function pushSubscribed(): Promise<boolean> {
  if (!pushSupported()) return false;
  const reg = await navigator.serviceWorker.ready;
  return !!(await reg.pushManager.getSubscription());
}

/** Ask for permission and subscribe. Throws a message to show when it can't. */
export async function enablePush(): Promise<void> {
  if (!pushSupported()) throw new Error("This browser doesn't support notifications.");
  const res = await fetch(`${API}/api/push/public-key`);
  const { public_key } = await res.json().catch(() => ({ public_key: "" }));
  if (!public_key) throw new Error("Notifications aren't set up yet.");
  if ((await Notification.requestPermission()) !== "granted") {
    throw new Error("Notifications are blocked for this site. Allow them in your browser's site settings.");
  }
  const reg = await navigator.serviceWorker.ready;
  const sub = (await reg.pushManager.getSubscription())
    ?? await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(public_key) });
  await send("POST", sub);
}

export async function disablePush(): Promise<void> {
  if (!pushSupported()) return;
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (!sub) return;
  await send("DELETE", sub).catch(() => undefined);
  await sub.unsubscribe();
}
