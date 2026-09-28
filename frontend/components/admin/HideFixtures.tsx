"use client";

import { useEffect, useMemo, useState } from "react";
import { EyeOff, Search, Undo2 } from "lucide-react";
import { Btn, inputClass, useAdmin } from "./ui";

// Take a fixture off the site when a source lists a match that isn't
// happening (backend /api/admin/matches/*). Each fixture shows where it came
// from, so a wrong one can be traced.

interface Fixture { home: string; away: string; date: string; time?: string; match_id?: string | number; league?: string; league_name?: string }
interface Hidden { home: string; away: string; date: string; at?: string; source?: string | null }

/** The source and competition, as the backend's _fixture_source. */
export function fixtureSource(p: Fixture): string {
  const mid = String(p.match_id ?? "");
  const prefix = mid.includes(":") ? mid.split(":")[0] : "";
  const src = ({ espn: "ESPN", sofa: "SofaScore", odds: "The Odds API" } as Record<string, string>)[prefix]
    ?? (/^\d+$/.test(mid) ? "football-data.org" : "");
  return [src, p.league_name || p.league].filter(Boolean).join(" · ") || "unknown";
}

export function HideFixtures({ preds, onChange }: { preds: Fixture[]; onChange?: () => void }) {
  const { get, post, flash } = useAdmin();
  const [q, setQ] = useState("");
  const [hidden, setHidden] = useState<Hidden[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const load = () => get("/api/admin/matches/hidden").then(d => setHidden(d?.hidden ?? []));
  useEffect(() => { load(); }, [get]); // eslint-disable-line react-hooks/exhaustive-deps

  const found = useMemo(() => !q.trim() ? [] : preds.filter(p =>
    `${p.home} ${p.away} ${p.league_name ?? ""}`.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 8), [preds, q]);
  const key = (p: { home: string; away: string; date: string }) => `${p.home}|${p.away}|${p.date}`;

  const hide = async (p: Fixture) => {
    if (!confirm(`Take ${p.home} v ${p.away} (${p.date}) off the site?`)) return;
    setBusy(key(p));
    try {
      const d = await post("/api/admin/matches/hide", { home: p.home, away: p.away, date: p.date });
      flash("ok", `Hidden: ${p.home} v ${p.away}${d?.removed_from_day ? ", and off the day's list" : ""}`);
      setQ(""); load(); onChange?.();
    } catch (e: any) { flash("err", e?.message || "Couldn't hide it"); }
    setBusy(null);
  };
  const unhide = async (h: Hidden) => {
    setBusy(key(h));
    try {
      await post("/api/admin/matches/unhide", { home: h.home, away: h.away, date: h.date });
      flash("ok", `${h.home} v ${h.away} comes back with the next predictions rebuild`);
      load();
    } catch { flash("err", "Couldn't put it back"); }
    setBusy(null);
  };

  return (
    <div className="space-y-3">
      <div className="relative">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-n-500" />
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Find a fixture (team or competition)" className={`${inputClass} pl-9`} />
      </div>
      {found.length > 0 && (
        <ul className="divide-y divide-n-800 rounded-xl border border-n-800">
          {found.map(p => (
            <li key={key(p)} className="flex items-center gap-3 px-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="text-sm text-n-0 truncate">{p.home} v {p.away}</p>
                <p className="text-[11px] text-n-500 truncate">{p.date}{p.time ? ` ${p.time} UTC` : ""} · from {fixtureSource(p)}</p>
              </div>
              <Btn variant="danger" busy={busy === key(p)} onClick={() => hide(p)}><EyeOff size={12} /> Hide</Btn>
            </li>
          ))}
        </ul>
      )}
      {q.trim() && found.length === 0 && <p className="text-xs text-n-400">No upcoming fixture matches.</p>}
      {hidden.length > 0 && (
        <div>
          <p className="eyebrow mb-1.5">Hidden</p>
          <ul className="space-y-1.5">
            {hidden.map(h => (
              <li key={key(h)} className="flex items-center gap-3 rounded-lg bg-surface-sunken px-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-n-200 truncate">{h.home} v {h.away}</p>
                  <p className="text-[11px] text-n-500 truncate">{h.date}{h.source ? ` · was from ${h.source}` : ""}</p>
                </div>
                <Btn busy={busy === key(h)} onClick={() => unhide(h)}><Undo2 size={12} /> Show again</Btn>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-[11px] text-n-500">
        For a match a source listed that isn&apos;t happening: it comes off the predictions, the day&apos;s list, the optimizer
        and daily odds, and stays off through every rebuild. Today&apos;s daily odds keep it until you remake them.
      </p>
    </div>
  );
}
