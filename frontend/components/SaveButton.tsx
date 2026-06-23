"use client";

import { useUser } from "@clerk/nextjs";
import { Star } from "lucide-react";
import { useState } from "react";
import clsx from "clsx";
import type { Prediction } from "@/lib/api";

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
  const key = predKey(prediction);
  const [saved, setSaved] = useState(() => savedKeys.has(key));
  const [loading, setLoading] = useState(false);

  const toggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!user || loading) return;
    setLoading(true);
    const next = !saved;
    setSaved(next); // optimistic
    try {
      const res = await fetch(`${API}/api/user/saves`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uid: user.id, prediction }),
      });
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
      className={clsx("transition-all disabled:opacity-50",
        saved ? "text-yellow-400" : "text-slate-500 hover:text-yellow-400")}>
      <Star size={size} fill={saved ? "currentColor" : "none"} />
    </button>
  );
}
