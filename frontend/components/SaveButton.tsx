"use client";

import { useUser } from "@clerk/nextjs";
import { Star } from "lucide-react";
import { useEffect, useState } from "react";
import clsx from "clsx";
import type { Prediction } from "@/lib/api";
import { useAuthedFetch } from "@/lib/useAuthedFetch";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface Props {
  prediction: Prediction;
  savedKeys: Set<string>;          // pre-loaded set from parent — no per-card fetch
  onToggle?: (key: string, saved: boolean) => void;
  size?: number;
}

export function predKey(p: { home: string; away: string; date: string }) {
  return `${p.home}:${p.away}:${p.date}`;
}

export function SaveButton({ prediction, savedKeys, onToggle, size = 14 }: Props) {
  const { user } = useUser();
  const authFetch = useAuthedFetch();
  const key = predKey(prediction);
  // Depend on the boolean, not the Set: parents may pass a new Set each render
  const savedInParent = savedKeys.has(key);
  const [saved, setSaved] = useState(savedInParent);
  const [loading, setLoading] = useState(false);

  // The parent's saved list usually arrives after first render
  useEffect(() => { setSaved(savedInParent); }, [savedInParent]);

  const toggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!user || loading) return;
    setLoading(true);
    const next = !saved;
    setSaved(next); // optimistic
    try {
      // Explicit target state (not a toggle), so a retry can't flip it back
      const res = await authFetch(`${API}/api/user/saves`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uid: user.id, prediction, saved: next }),
      });
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      setSaved(data.saved);
      onToggle?.(key, data.saved);
    } catch {
      setSaved(!next); // revert on error
    } finally {
      setLoading(false);
    }
  };

  if (!user) return null;

  return (
    <button onClick={toggle} disabled={loading}
      title={saved ? "Remove from saved" : "Save pick"}
      aria-label={saved ? `Remove ${prediction.home} vs ${prediction.away} from saved` : `Save ${prediction.home} vs ${prediction.away}`}
      aria-pressed={saved}
      className={clsx("transition-all disabled:opacity-50",
        saved ? "text-amber-500 dark:text-yellow-400" : "text-n-500 hover:text-amber-500 dark:hover:text-yellow-400")}>
      <Star size={size} fill={saved ? "currentColor" : "none"} />
    </button>
  );
}
