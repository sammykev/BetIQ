"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { Bell, BellOff, Loader2 } from "lucide-react";
import { disablePush, enablePush, pushSubscribed, pushSupported } from "@/lib/push";

/** "Notify me when the daily odds are out": on/off for this browser. */
export function PushToggle({ compact = false, className }: { compact?: boolean; className?: string }) {
  const [supported, setSupported] = useState(false);
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pushSupported()) return;
    setSupported(true);
    pushSubscribed().then(setOn).catch(() => undefined);
  }, []);

  if (!supported) return null;

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      if (on) { await disablePush(); setOn(false); }
      else { await enablePush(); setOn(true); }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't change notifications.");
    } finally {
      setBusy(false);
    }
  }

  const Icon = busy ? Loader2 : on ? Bell : BellOff;
  return (
    <div className={className}>
      <button
        onClick={toggle}
        disabled={busy}
        title={on ? "Stop notifications on this device" : "Get a notification on this device when the daily odds are out"}
        className={clsx(
          "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all disabled:opacity-60",
          on ? "bg-brand-400/10 border-brand-400/40 text-accent"
             : "bg-surface border-n-800 text-n-400 hover:text-n-0 hover:border-n-700"
        )}
      >
        <Icon size={12} className={clsx(busy && "animate-spin")} />
        {compact ? (on ? "Alerts on" : "Alerts") : on ? "Notifying you when the odds are out" : "Notify me when the odds are out"}
      </button>
      {error && <p className="mt-1.5 text-xs text-danger">{error}</p>}
    </div>
  );
}
