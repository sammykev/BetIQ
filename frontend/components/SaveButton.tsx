"use client";

import { useUser } from "@clerk/nextjs";
import { Star } from "lucide-react";
import { useEffect, useState } from "react";
import clsx from "clsx";
import type { Prediction } from "@/lib/api";

const API = process.env.NEXT_PUBLIC_API_URL || "https://betiq-backend-jcwa.onrender.com";

interface Props {
  prediction: Prediction;
  size?: number;
}

export function SaveButton({ prediction, size = 14 }: Props) {
  const { user } = useUser();
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!user) return;
    fetch(`${API}/api/user/saves?uid=${encodeURIComponent(user.id)}`)
      .then(r => r.json())
      .then((saves: Prediction[]) => {
        const key = `${prediction.home}:${prediction.away}:${prediction.date}`;
        setSaved(saves.some((s: any) => `${s.home}:${s.away}:${s.date}` === key));
      })
      .catch(() => {});
  }, [user, prediction.home, prediction.away, prediction.date]);

  const toggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!user || loading) return;
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/user/saves`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uid: user.id, prediction }),
      });
      const data = await res.json();
      setSaved(data.saved);
    } catch { /* silently fail */ }
    finally { setLoading(false); }
  };

  if (!user) return null;

  return (
    <button
      onClick={toggle}
      disabled={loading}
      title={saved ? "Remove from saved" : "Save pick"}
      className={clsx(
        "transition-all disabled:opacity-50",
        saved ? "text-yellow-400" : "text-slate-500 hover:text-yellow-400"
      )}
    >
      <Star size={size} fill={saved ? "currentColor" : "none"} />
    </button>
  );
}
